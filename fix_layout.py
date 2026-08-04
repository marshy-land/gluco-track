import re

html = open('templates/index.html', encoding='utf-8').read()

p1_start = html.find('<!-- Upload/Import Area -->')
p1_end = html.find('<!-- Quick Log Insulin Dose -->')

p2_start = p1_end
p2_end = html.find('<!-- Quick Log Food / Carbs -->')

p3_start = p2_end
p3_end = html.find('<!-- Tuning & Heuristics -->')

p4_start = p3_end
p4_end = html.find('<!-- Circadian Nutritional Impact Panel -->')

actions_grid_start = html.find('<!-- Actions Grid (Upload and Log) -->')
# We need to find the closing div of the actions grid, which is before Circadian
actions_grid_end = p4_end

p1 = html[p1_start:p1_end].replace('            </div>            <!-- Quick Log Insulin Dose -->', '            </div>\n').strip()
p2 = html[p2_start:p2_end].strip()
p3 = html[p3_start:p3_end].strip()
p4 = html[p4_start:p4_end].strip()

# Find the closing </div> of actions grid inside p4 string
last_div_idx = p4.rfind('</div>')
if last_div_idx != -1:
    p4 = p4[:last_div_idx].strip()

new_grid = f"""
        <!-- Action Logging Grid -->
        <div style="display: grid; gap: 1.5rem;" class="actions-grid">
            {p2}
            
            {p3}
        </div>

        <!-- Advanced / System Grid -->
        <h2 style="margin-top: 2.5rem; margin-bottom: -0.5rem; font-family: 'Outfit', sans-serif; font-size: 1.5rem; color: var(--text-secondary);">Advanced Settings</h2>
        <div style="display: grid; gap: 1.5rem; margin-top: 1rem;" class="actions-grid">
            {p1}
            
            {p4}
        </div>
"""

new_html = html[:actions_grid_start] + new_grid + "\n        " + html[actions_grid_end:]
open('templates/index.html', 'w', encoding='utf-8').write(new_html)
print("Done")
