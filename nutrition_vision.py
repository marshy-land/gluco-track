import os
import re
import json
import base64
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
import db

load_dotenv()

# Built-in clinical carbohydrate reference database (grams per standard serving)
FOOD_CARB_DATABASE = {
    # Breads & Grains
    "bread": {"carbs": 15.0, "unit": "slice", "aliases": ["toast", "white bread", "wheat bread", "multigrain"]},
    "sourdough": {"carbs": 18.0, "unit": "slice", "aliases": ["sourdough bread", "sourdough toast"]},
    "bagel": {"carbs": 50.0, "unit": "bagel", "aliases": ["plain bagel", "everything bagel"]},
    "rice": {"carbs": 45.0, "unit": "cup", "aliases": ["white rice", "brown rice", "jasmine rice", "basmati rice"]},
    "pasta": {"carbs": 40.0, "unit": "cup", "aliases": ["spaghetti", "penne", "macaroni", "noodles", "fettuccine"]},
    "oatmeal": {"carbs": 28.0, "unit": "cup", "aliases": ["oats", "porridge", "rolled oats"]},
    "cereal": {"carbs": 30.0, "unit": "bowl", "aliases": ["cornflakes", "cheerios", "granola", "muesli"]},
    "quinoa": {"carbs": 39.0, "unit": "cup", "aliases": []},
    "tortilla": {"carbs": 20.0, "unit": "tortilla", "aliases": ["wrap", "flour tortilla", "corn tortilla"]},
    "croissant": {"carbs": 26.0, "unit": "croissant", "aliases": ["pastry"]},
    "pancake": {"carbs": 22.0, "unit": "pancake", "aliases": ["waffle", "pancakes", "waffles"]},

    # Fruits
    "apple": {"carbs": 22.0, "unit": "medium", "aliases": ["apples", "green apple", "red apple"]},
    "banana": {"carbs": 27.0, "unit": "medium", "aliases": ["bananas"]},
    "orange": {"carbs": 15.0, "unit": "medium", "aliases": ["oranges", "clementine", "tangerine"]},
    "berries": {"carbs": 14.0, "unit": "cup", "aliases": ["strawberries", "blueberries", "raspberries", "blackberries"]},
    "grapes": {"carbs": 28.0, "unit": "cup", "aliases": ["grape"]},
    "watermelon": {"carbs": 12.0, "unit": "slice", "aliases": ["melon", "cantaloupe", "honeydew"]},
    "peach": {"carbs": 14.0, "unit": "medium", "aliases": ["nectarine", "plum"]},
    "mango": {"carbs": 35.0, "unit": "cup", "aliases": ["mangoes"]},
    "pineapple": {"carbs": 22.0, "unit": "cup", "aliases": []},

    # Vegetables & Starches
    "potato": {"carbs": 30.0, "unit": "medium", "aliases": ["baked potato", "mashed potato", "potatoes", "fries", "french fries"]},
    "sweet potato": {"carbs": 24.0, "unit": "medium", "aliases": ["yam", "sweet potatoes"]},
    "corn": {"carbs": 25.0, "unit": "ear", "aliases": ["corn on the cob", "sweet corn", "corn cup"]},
    "beans": {"carbs": 22.0, "unit": "cup", "aliases": ["black beans", "kidney beans", "pinto beans", "chickpeas", "lentils"]},
    "salad": {"carbs": 5.0, "unit": "bowl", "aliases": ["greens", "lettuce", "caesar salad", "garden salad"]},
    "broccoli": {"carbs": 6.0, "unit": "cup", "aliases": ["cauliflower", "asparagus", "green beans", "spinach"]},

    # Meals, Fast Food & Dishes
    "pizza": {"carbs": 32.0, "unit": "slice", "aliases": ["slice of pizza", "pepperoni pizza", "cheese pizza"]},
    "burger": {"carbs": 35.0, "unit": "burger", "aliases": ["cheeseburger", "hamburger", "chicken burger"]},
    "sandwich": {"carbs": 35.0, "unit": "sandwich", "aliases": ["sub", "blt", "turkey sandwich", "ham sandwich"]},
    "taco": {"carbs": 15.0, "unit": "taco", "aliases": ["tacos"]},
    "burrito": {"carbs": 55.0, "unit": "burrito", "aliases": ["burrito bowl"]},
    "sushi": {"carbs": 30.0, "unit": "roll (6pcs)", "aliases": ["sushi roll", "california roll"]},
    "soup": {"carbs": 18.0, "unit": "bowl", "aliases": ["chicken noodle soup", "vegetable soup", "tomato soup"]},

    # Dairy, Snacks & Sweets
    "yogurt": {"carbs": 16.0, "unit": "cup", "aliases": ["greek yogurt", "vanilla yogurt"]},
    "milk": {"carbs": 12.0, "unit": "cup", "aliases": ["whole milk", "almond milk", "oat milk", "soy milk"]},
    "ice cream": {"carbs": 32.0, "unit": "scoop", "aliases": ["gelato", "frozen yogurt"]},
    "cookie": {"carbs": 18.0, "unit": "cookie", "aliases": ["cookies", "biscuit", "biscuits", "chocolate chip cookie"]},
    "chocolate": {"carbs": 25.0, "unit": "bar", "aliases": ["candy bar", "candy", "chocolate bar", "snickers"]},
    "chips": {"carbs": 20.0, "unit": "bag", "aliases": ["crisps", "potato chips", "tortilla chips", "doritos"]},
    "soda": {"carbs": 39.0, "unit": "can", "aliases": ["coke", "pepsi", "sprite", "juice", "orange juice", "apple juice"]},

    # Proteins (Negligible Carbs)
    "egg": {"carbs": 0.6, "unit": "egg", "aliases": ["eggs", "boiled egg", "scrambled egg", "fried egg"]},
    "chicken": {"carbs": 0.0, "unit": "breast", "aliases": ["grilled chicken", "chicken breast", "chicken thigh", "wings"]},
    "steak": {"carbs": 0.0, "unit": "steak", "aliases": ["beef", "roast beef", "pork", "pork chop", "meat"]},
    "salmon": {"carbs": 0.0, "unit": "fillet", "aliases": ["fish", "tuna", "shrimp", "seafood"]},
    "cheese": {"carbs": 1.0, "unit": "slice", "aliases": ["cheddar", "mozzarella", "parmesan", "swiss cheese"]},
    "nuts": {"carbs": 5.0, "unit": "handful", "aliases": ["almonds", "walnuts", "peanuts", "cashews", "peanut butter"]}
}

def get_gemini_api_key():
    """Fetches Gemini / Google AI API key from env or db settings."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        stored = db.get_system_setting("gemini_config")
        if stored and isinstance(stored, dict):
            key = stored.get("api_key")
    return key.strip() if key else None

def estimate_carbohydrates_from_text(user_text):
    """
    Parses natural language food descriptions and estimates total carbohydrates.
    Returns: { 'carbs_g': float, 'description': str, 'items': list, 'confidence': float }
    """
    text = user_text.lower().strip()
    # Strip prefixes like "ate", "eating", "had", "logging", "food:"
    text = re.sub(r'^(?:i\s+)?(?:ate|had|eating|having|log|logged|logging|food:?|meal:?)\s*', '', text).strip()

    # 1. Direct explicit carb entry (e.g. "45g carbs", "ate 30 grams of carbs")
    explicit_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:g|grams?)\s*(?:of)?\s*(?:carbs?|carbohydrates?)', text)
    if explicit_match:
        try:
            carbs = float(explicit_match.group(1))
            return {
                "carbs_g": round(carbs, 1),
                "description": f"Custom entry ({carbs:.0f}g carbs)",
                "items": [{"name": "Direct Carb Input", "quantity": f"{carbs:.0f}g", "carbs_g": carbs}],
                "confidence": 1.0,
                "note": "Exact user-specified carbohydrate amount."
            }
        except Exception:
            pass

    # 2. Try Gemini API first if configured
    gemini_key = get_gemini_api_key()
    if gemini_key:
        try:
            res = call_gemini_text_nutrition(text, gemini_key)
            if res and res.get("carbs_g") is not None:
                return res
        except Exception as e:
            print(f"[NutritionVision] Gemini text estimation warning: {e}")

    # 3. Comprehensive onboard rule-based & dictionary estimation
    found_items = []
    total_carbs = 0.0

    # Extract quantities (e.g., "2 slices", "1/2 cup", "3", "a bowl of")
    number_words = {
        "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
        "six": 6.0, "seven": 7.0, "eight": 8.0, "half": 0.5, "a": 1.0, "an": 1.0
    }

    # Split by conjunctions like "and", "with", ",", "+"
    parts = re.split(r'[,+]|\band\b|\bwith\b', text)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Look for leading quantity
        qty = 1.0
        qty_match = re.search(r'^(?:(\d+(?:\.\d+)?)|(one|two|three|four|five|six|seven|eight|half|a|an))\b', part)
        if qty_match:
            if qty_match.group(1):
                qty = float(qty_match.group(1))
            elif qty_match.group(2):
                qty = number_words.get(qty_match.group(2), 1.0)

        # Match against database items & aliases
        matched = False
        for key, data in FOOD_CARB_DATABASE.items():
            names_to_check = [key] + data.get("aliases", [])
            for name in names_to_check:
                if re.search(r'\b' + re.escape(name) + r'\b', part):
                    item_carbs = data["carbs"] * qty
                    found_items.append({
                        "name": key.title(),
                        "quantity": f"{qty:.1f}".rstrip('0').rstrip('.') + f" {data['unit']}(s)" if data['unit'] else f"{qty}",
                        "carbs_g": round(item_carbs, 1)
                    })
                    total_carbs += item_carbs
                    matched = True
                    break
            if matched:
                break

    if found_items:
        return {
            "carbs_g": round(total_carbs, 1),
            "description": ", ".join([f"{it['quantity']} {it['name']} ({it['carbs_g']}g)" for it in found_items]),
            "items": found_items,
            "confidence": 0.88,
            "note": "Estimated using clinical carbohydrate reference database."
        }

    # 4. Fallback estimation for generic snack/meal
    return {
        "carbs_g": 30.0,
        "description": f"Standard Meal: '{user_text}' (~30g carbs estimated)",
        "items": [{"name": user_text.title(), "quantity": "1 serving", "carbs_g": 30.0}],
        "confidence": 0.60,
        "note": "Default average meal carb estimate. Tap below to confirm or adjust."
    }

def analyze_food_photo(photo_bytes, caption=None):
    """
    Analyzes a meal photo using Vision AI to identify foods, portions, and total carbohydrates.
    """
    gemini_key = get_gemini_api_key()
    if gemini_key:
        try:
            return call_gemini_vision_nutrition(photo_bytes, caption, gemini_key)
        except Exception as e:
            print(f"[NutritionVision] Gemini Vision error: {e}")

    # Fallback if no Gemini key is provided
    return {
        "carbs_g": 40.0,
        "description": "Visual Meal Capture (Image Received)",
        "items": [
            {"name": "Plate Analysis (Standard Portion)", "quantity": "1 plate", "carbs_g": 40.0}
        ],
        "confidence": 0.70,
        "note": "Photo received. Estimated ~40g carbs standard meal. Add a GEMINI_API_KEY in settings for granular AI visual plate recognition."
    }

def call_gemini_text_nutrition(food_text, api_key):
    """Calls Gemini 1.5 Flash via REST API for deep nutritional analysis."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    prompt = (
        f"You are a clinical dietitian specializing in Type 1 Diabetes carbohydrate counting.\n"
        f"Estimate the carbohydrates for this meal: '{food_text}'.\n"
        f"Output ONLY valid JSON without markdown wrapping with keys:\n"
        f"- total_carbs_g: (float, total net carbs)\n"
        f"- description: (string, brief summary of meal items and portions)\n"
        f"- items: (list of objects with keys: name, quantity, carbs_g)\n"
        f"- glycemic_index: (string: Low, Medium, High)\n"
        f"- clinical_note: (string, brief explanation of insulin timing)"
    )
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
    }
    
    resp = requests.post(url, json=payload, timeout=12)
    if resp.ok:
        res_data = resp.json()
        candidates = res_data.get("candidates", [])
        if candidates:
            text_out = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            parsed = json.loads(text_out)
            return {
                "carbs_g": round(float(parsed.get("total_carbs_g", 30.0)), 1),
                "description": parsed.get("description", food_text),
                "items": parsed.get("items", []),
                "confidence": 0.95,
                "note": f"GI: {parsed.get('glycemic_index', 'Medium')} — {parsed.get('clinical_note', '')}"
            }
    return None

def call_gemini_vision_nutrition(photo_bytes, caption, api_key):
    """Calls Gemini 1.5 Flash with image payload for computer vision carbohydrate counting."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    b64_image = base64.b64encode(photo_bytes).decode('utf-8')
    
    prompt = (
        "You are an expert clinical dietitian and computer vision nutritionist for a patient managing diabetes.\n"
        "Analyze this meal photo meticulously.\n"
        "1. Identify each distinct food item visible on the plate/table.\n"
        "2. Estimate the visual volume and portion sizes (e.g. 1 cup, 2 slices, 150g).\n"
        "3. Calculate the grams of carbohydrates (carbs_g) for each component and the total carbs.\n"
        "4. Note the estimated glycemic index and whether pre-bolusing 15m before is recommended.\n"
        f"Additional user caption context: '{caption or 'None'}'\n\n"
        "Output ONLY strict JSON with this exact schema:\n"
        "{\n"
        "  \"total_carbs_g\": float,\n"
        "  \"description\": string,\n"
        "  \"items\": [{\"name\": string, \"quantity\": string, \"carbs_g\": float}],\n"
        "  \"glycemic_index\": string,\n"
        "  \"clinical_advice\": string\n"
        "}"
    )
    
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}}
            ]
        }],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
    }
    
    resp = requests.post(url, json=payload, timeout=20)
    if resp.ok:
        res_data = resp.json()
        candidates = res_data.get("candidates", [])
        if candidates:
            text_out = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            parsed = json.loads(text_out)
            return {
                "carbs_g": round(float(parsed.get("total_carbs_g", 35.0)), 1),
                "description": parsed.get("description", "Identified visual meal components"),
                "items": parsed.get("items", []),
                "confidence": 0.95,
                "note": f"GI: {parsed.get('glycemic_index', 'Medium')}. {parsed.get('clinical_advice', '')}"
            }
            
    print(f"[NutritionVision] Gemini Vision API response failed ({resp.status_code}): {resp.text}")
    return None
