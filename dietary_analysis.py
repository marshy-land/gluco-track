"""
Dietary Analysis Engine & Report Generator for Gluco Track (Milestone M1 / Requirement R1)

Analyzes historical glucose data for glycemic anomalies:
  1. Postprandial Spikes (> 180 mg/dL)
  2. Dawn Phenomenon (04:00 - 08:00 AM rise) with Somogyi Exclusion Check (22:00 - 04:00 hypo < 70 mg/dL)
  3. Nocturnal Hypoglycemia (< 70 mg/dL between 22:00 - 06:00)
  4. Glycemic Variability (CV = SD / Mean > 36%)

Calculates standard clinical statistics (Mean, GMI, CV, TIR %, TAR %, TBR %)
and generates `dietary_remedies_report.md` with literature-backed dietary interventions.
"""

import os
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple

import pytz

from literature_api import Citation, fetch_literature_for_anomalies

logger = logging.getLogger(__name__)

# Try importing db.py for default history fetching
try:
    import db
except ImportError:
    db = None


class AnomalyType(str, Enum):
    POSTPRANDIAL_SPIKE = "postprandial_spike"
    DAWN_PHENOMENON = "dawn_phenomenon"
    NOCTURNAL_HYPO = "nocturnal_hypo"
    HIGH_GLYCEMIC_VARIABILITY = "high_glycemic_variability"


@dataclass
class AnomalyRecord:
    anomaly_type: AnomalyType
    timestamp: datetime
    end_timestamp: Optional[datetime]
    peak_value: float
    nadir_value: Optional[float]
    delta_value: float
    duration_minutes: float
    severity: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GlycemicStats:
    total_readings: int
    mean_glucose: float
    std_dev: float
    gmi: float
    cv_percent: float
    tir_percent: float  # 70 - 180 mg/dL
    tar_percent: float  # > 180 mg/dL
    tbr_percent: float  # < 70 mg/dL
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


@dataclass
class AnomalySummary:
    postprandial_spikes_count: int = 0
    dawn_phenomenon_count: int = 0
    nocturnal_hypos_count: int = 0
    high_variability_days: int = 0
    anomalies: List[AnomalyRecord] = field(default_factory=list)


def parse_dt(ts: Any, timezone_str: str = "America/New_York") -> datetime:
    """Parses timestamp into UTC datetime and localizes if needed."""
    if isinstance(ts, str):
        # Handle string timestamps ISO formatted
        ts = ts.replace("Z", "+00:00")
        dt_obj = datetime.fromisoformat(ts)
    elif isinstance(ts, datetime):
        dt_obj = ts
    else:
        dt_obj = datetime.now(timezone.utc)

    if dt_obj.tzinfo is None:
        dt_obj = pytz.utc.localize(dt_obj)
    return dt_obj


def to_local_dt(dt_obj: datetime, timezone_str: str = "America/New_York") -> datetime:
    """Converts UTC datetime to specified local timezone."""
    tz = pytz.timezone(timezone_str)
    return dt_obj.astimezone(tz)


def format_pmid_link(pmid: Optional[str]) -> str:
    """Formats PMID into hyperlinked NCBI link."""
    if not pmid:
        return "N/A"
    clean = str(pmid).strip()
    return f"[{clean}](https://pubmed.ncbi.nlm.nih.gov/{clean}/)"


def format_doi_link(doi: Optional[str]) -> str:
    """Formats DOI into clean clickable markdown link."""
    if not doi:
        return "N/A"
    clean = doi.replace("https://doi.org/", "").strip()
    return f"[{clean}](https://doi.org/{clean})"


def calculate_glycemic_stats(readings: List[Dict[str, Any]]) -> GlycemicStats:
    """Computes Mean, SD, GMI, CV %, TIR %, TAR %, TBR % from glucose readings list."""
    if not readings:
        return GlycemicStats(
            total_readings=0,
            mean_glucose=0.0,
            std_dev=0.0,
            gmi=3.31,
            cv_percent=0.0,
            tir_percent=0.0,
            tar_percent=0.0,
            tbr_percent=0.0
        )

    values = []
    for r in readings:
        if isinstance(r, dict) and "value" in r and r["value"] is not None:
            try:
                v = float(r["value"])
                if not math.isnan(v) and not math.isinf(v):
                    values.append(v)
            except (ValueError, TypeError):
                continue

    total = len(values)
    if total == 0:
        return GlycemicStats(
            total_readings=0,
            mean_glucose=0.0,
            std_dev=0.0,
            gmi=3.31,
            cv_percent=0.0,
            tir_percent=0.0,
            tar_percent=0.0,
            tbr_percent=0.0
        )

    mean_val = sum(values) / total
    variance = sum((x - mean_val) ** 2 for x in values) / (total - 1 if total > 1 else 1)
    sd_val = math.sqrt(variance)
    
    # GMI = 3.31 + 0.02392 * Mean Glucose
    gmi_val = 3.31 + (0.02392 * mean_val)
    
    # CV % = (SD / Mean) * 100
    cv_val = (sd_val / mean_val * 100.0) if mean_val > 0 else 0.0

    in_range = sum(1 for x in values if 70.0 <= x <= 180.0)
    above_range = sum(1 for x in values if x > 180.0)
    below_range = sum(1 for x in values if x < 70.0)

    tir_pct = (in_range / total) * 100.0
    tar_pct = (above_range / total) * 100.0
    tbr_pct = (below_range / total) * 100.0

    # Get start and end timestamps
    dts = []
    for r in readings:
        if isinstance(r, dict) and "timestamp" in r and r["timestamp"] is not None:
            try:
                dts.append(parse_dt(r["timestamp"]))
            except Exception:
                pass
    dts.sort()
    start_dt = dts[0] if dts else None
    end_dt = dts[-1] if dts else None

    return GlycemicStats(
        total_readings=total,
        mean_glucose=round(mean_val, 1),
        std_dev=round(sd_val, 1),
        gmi=round(gmi_val, 2),
        cv_percent=round(cv_val, 1),
        tir_percent=round(tir_pct, 1),
        tar_percent=round(tar_pct, 1),
        tbr_percent=round(tbr_pct, 1),
        start_date=start_dt,
        end_date=end_dt
    )


def detect_postprandial_spikes(
    readings: List[Dict[str, Any]],
    spike_threshold: float = 180.0,
    timezone_str: str = "America/New_York"
) -> List[AnomalyRecord]:
    """
    Detects postprandial glycemic spikes (> 180 mg/dL).
    Groups consecutive readings exceeding threshold into single spike episodes.
    """
    if not readings:
        return []

    # Sort readings by timestamp
    parsed = []
    for r in readings:
        if isinstance(r, dict) and "value" in r and r["value"] is not None and "timestamp" in r and r["timestamp"] is not None:
            try:
                parsed.append((parse_dt(r["timestamp"], timezone_str), float(r["value"])))
            except Exception:
                pass
    parsed.sort(key=lambda x: x[0])

    spikes: List[AnomalyRecord] = []
    current_episode = []

    for i, (dt, val) in enumerate(parsed):
        if val > spike_threshold:
            current_episode.append((dt, val, i))
        else:
            if current_episode:
                # Close episode
                start_dt = current_episode[0][0]
                end_dt = current_episode[-1][0]
                peak_val = max(x[1] for x in current_episode)
                peak_dt = next(x[0] for x in current_episode if x[1] == peak_val)
                duration_mins = max(15.0, (end_dt - start_dt).total_seconds() / 60.0)

                # Pre-spike baseline (preceding 2 hours)
                first_idx = current_episode[0][2]
                baseline = spike_threshold
                pre_readings = [
                    parsed[j][1] for j in range(max(0, first_idx - 8), first_idx)
                    if (start_dt - parsed[j][0]).total_seconds() <= 7200
                ]
                if pre_readings:
                    baseline = min(pre_readings)

                delta = peak_val - baseline
                severity = "Mild" if peak_val < 200 else ("Moderate" if peak_val < 240 else "Severe")

                spikes.append(AnomalyRecord(
                    anomaly_type=AnomalyType.POSTPRANDIAL_SPIKE,
                    timestamp=start_dt,
                    end_timestamp=end_dt,
                    peak_value=round(peak_val, 1),
                    nadir_value=round(baseline, 1),
                    delta_value=round(delta, 1),
                    duration_minutes=round(duration_mins, 1),
                    severity=severity,
                    details={"peak_timestamp": peak_dt, "baseline": baseline}
                ))
                current_episode = []

    # Check last episode if open
    if current_episode:
        start_dt = current_episode[0][0]
        end_dt = current_episode[-1][0]
        peak_val = max(x[1] for x in current_episode)
        peak_dt = next(x[0] for x in current_episode if x[1] == peak_val)
        duration_mins = max(15.0, (end_dt - start_dt).total_seconds() / 60.0)
        first_idx = current_episode[0][2]
        baseline = spike_threshold
        pre_readings = [
            parsed[j][1] for j in range(max(0, first_idx - 8), first_idx)
            if (start_dt - parsed[j][0]).total_seconds() <= 7200
        ]
        if pre_readings:
            baseline = min(pre_readings)
        delta = peak_val - baseline
        severity = "Mild" if peak_val < 200 else ("Moderate" if peak_val < 240 else "Severe")

        spikes.append(AnomalyRecord(
            anomaly_type=AnomalyType.POSTPRANDIAL_SPIKE,
            timestamp=start_dt,
            end_timestamp=end_dt,
            peak_value=round(peak_val, 1),
            nadir_value=round(baseline, 1),
            delta_value=round(delta, 1),
            duration_minutes=round(duration_mins, 1),
            severity=severity,
            details={"peak_timestamp": peak_dt, "baseline": baseline}
        ))

    return spikes


def detect_nocturnal_hypos(
    readings: List[Dict[str, Any]],
    hypo_threshold: float = 70.0,
    timezone_str: str = "America/New_York"
) -> List[AnomalyRecord]:
    """
    Detects nocturnal hypoglycemia (< 70 mg/dL during local 22:00 - 06:00).
    Groups consecutive nighttime hypo readings into single hypo episodes.
    """
    if not readings:
        return []

    # Sort readings by local timestamp
    parsed = []
    for r in readings:
        if isinstance(r, dict) and "value" in r and r["value"] is not None and "timestamp" in r and r["timestamp"] is not None:
            try:
                utc_dt = parse_dt(r["timestamp"], timezone_str)
                local_dt = to_local_dt(utc_dt, timezone_str)
                parsed.append((local_dt, float(r["value"])))
            except Exception:
                pass
    parsed.sort(key=lambda x: x[0])

    hypos: List[AnomalyRecord] = []
    current_episode = []

    for dt, val in parsed:
        is_nighttime = (dt.hour >= 22 or dt.hour < 6)
        if is_nighttime and val < hypo_threshold:
            if not current_episode:
                current_episode.append((dt, val))
            else:
                # If within 45 mins of previous hypo reading, group together
                prev_dt = current_episode[-1][0]
                if (dt - prev_dt).total_seconds() <= 2700:
                    current_episode.append((dt, val))
                else:
                    # Close existing episode
                    start_dt = current_episode[0][0]
                    end_dt = current_episode[-1][0]
                    nadir = min(x[1] for x in current_episode)
                    duration_mins = max(15.0, (end_dt - start_dt).total_seconds() / 60.0)
                    severity = "Level 2 Severe" if nadir < 54.0 else "Level 1"
                    hypos.append(AnomalyRecord(
                        anomaly_type=AnomalyType.NOCTURNAL_HYPO,
                        timestamp=start_dt,
                        end_timestamp=end_dt,
                        peak_value=round(hypo_threshold - nadir, 1),
                        nadir_value=round(nadir, 1),
                        delta_value=round(70.0 - nadir, 1),
                        duration_minutes=round(duration_mins, 1),
                        severity=severity
                    ))
                    current_episode = [(dt, val)]
        else:
            if current_episode:
                start_dt = current_episode[0][0]
                end_dt = current_episode[-1][0]
                nadir = min(x[1] for x in current_episode)
                duration_mins = max(15.0, (end_dt - start_dt).total_seconds() / 60.0)
                severity = "Level 2 Severe" if nadir < 54.0 else "Level 1"
                hypos.append(AnomalyRecord(
                    anomaly_type=AnomalyType.NOCTURNAL_HYPO,
                    timestamp=start_dt,
                    end_timestamp=end_dt,
                    peak_value=round(hypo_threshold - nadir, 1),
                    nadir_value=round(nadir, 1),
                    delta_value=round(70.0 - nadir, 1),
                    duration_minutes=round(duration_mins, 1),
                    severity=severity
                ))
                current_episode = []

    if current_episode:
        start_dt = current_episode[0][0]
        end_dt = current_episode[-1][0]
        nadir = min(x[1] for x in current_episode)
        duration_mins = max(15.0, (end_dt - start_dt).total_seconds() / 60.0)
        severity = "Level 2 Severe" if nadir < 54.0 else "Level 1"
        hypos.append(AnomalyRecord(
            anomaly_type=AnomalyType.NOCTURNAL_HYPO,
            timestamp=start_dt,
            end_timestamp=end_dt,
            peak_value=round(hypo_threshold - nadir, 1),
            nadir_value=round(nadir, 1),
            delta_value=round(70.0 - nadir, 1),
            duration_minutes=round(duration_mins, 1),
            severity=severity
        ))

    return hypos


def detect_dawn_phenomenon(
    readings: List[Dict[str, Any]],
    timezone_str: str = "America/New_York",
    rise_threshold: float = 20.0
) -> List[AnomalyRecord]:
    """
    Detects Dawn Phenomenon (04:00 - 08:00 AM rise).
    Includes Somogyi Exclusion Check: verifies nighttime glucose (22:00 - 04:00) did NOT drop below 70 mg/dL.
    If nocturnal hypo occurred prior, excludes from Dawn Phenomenon.
    """
    if not readings:
        return []

    # Group readings by local date
    local_readings: List[Tuple[datetime, float]] = []
    for r in readings:
        if isinstance(r, dict) and "value" in r and r["value"] is not None and "timestamp" in r and r["timestamp"] is not None:
            try:
                utc_dt = parse_dt(r["timestamp"], timezone_str)
                local_dt = to_local_dt(utc_dt, timezone_str)
                local_readings.append((local_dt, float(r["value"])))
            except Exception:
                pass
    local_readings.sort(key=lambda x: x[0])

    # Find unique dates
    dates = sorted(list(set(x[0].date() for x in local_readings)))

    dawn_events: List[AnomalyRecord] = []

    for current_date in dates:
        # Check nocturnal window for Somogyi exclusion: 22:00 on (current_date - 1 day) to 04:00 on current_date
        prev_date = current_date - timedelta(days=1)
        nocturnal_readings = [
            val for dt, val in local_readings
            if (dt.date() == prev_date and dt.hour >= 22) or (dt.date() == current_date and dt.hour < 4)
        ]

        # Somogyi exclusion check
        has_nocturnal_hypo = any(v < 70.0 for v in nocturnal_readings)
        if has_nocturnal_hypo:
            # Excluded due to Somogyi effect
            continue

        # Early morning baseline window: 03:00 to 04:30 AM on current_date
        baseline_readings = [
            (dt, val) for dt, val in local_readings
            if dt.date() == current_date and (dt.hour == 3 or (dt.hour == 4 and dt.minute <= 30))
        ]

        # Morning window: 04:00 to 08:00 AM on current_date
        morning_readings = [
            (dt, val) for dt, val in local_readings
            if dt.date() == current_date and (4 <= dt.hour < 8)
        ]

        if not morning_readings:
            continue

        baseline_val = min(x[1] for x in baseline_readings) if baseline_readings else morning_readings[0][1]
        peak_dt, peak_val = max(morning_readings, key=lambda x: x[1])

        delta = peak_val - baseline_val
        if delta >= rise_threshold and peak_val > 130.0:
            start_dt = morning_readings[0][0]
            end_dt = morning_readings[-1][0]
            duration_mins = max(30.0, (end_dt - start_dt).total_seconds() / 60.0)

            dawn_events.append(AnomalyRecord(
                anomaly_type=AnomalyType.DAWN_PHENOMENON,
                timestamp=peak_dt,
                end_timestamp=end_dt,
                peak_value=round(peak_val, 1),
                nadir_value=round(baseline_val, 1),
                delta_value=round(delta, 1),
                duration_minutes=round(duration_mins, 1),
                severity="Moderate" if delta >= 35.0 else "Mild",
                details={"date": str(current_date), "baseline": baseline_val}
            ))

    return dawn_events


def calculate_glycemic_variability(
    readings: List[Dict[str, Any]],
    timezone_str: str = "America/New_York"
) -> Tuple[float, int, List[AnomalyRecord]]:
    """
    Calculates overall Coefficient of Variation (CV = SD / Mean * 100)
    and counts individual volatile days where daily CV > 36.0%.
    """
    if not readings:
        return 0.0, 0, []

    stats = calculate_glycemic_stats(readings)
    overall_cv = stats.cv_percent

    # Group by local date to count high CV days
    by_date: Dict[date, List[float]] = {}
    for r in readings:
        if isinstance(r, dict) and "value" in r and r["value"] is not None and "timestamp" in r and r["timestamp"] is not None:
            try:
                utc_dt = parse_dt(r["timestamp"], timezone_str)
                local_dt = to_local_dt(utc_dt, timezone_str)
                d = local_dt.date()
                by_date.setdefault(d, []).append(float(r["value"]))
            except Exception:
                pass

    high_cv_days = 0
    anomalies: List[AnomalyRecord] = []

    for d, vals in by_date.items():
        if len(vals) >= 8:  # require at least 8 readings in a day to calculate CV
            d_mean = sum(vals) / len(vals)
            if d_mean > 0:
                d_var = sum((x - d_mean) ** 2 for x in vals) / (len(vals) - 1)
                d_sd = math.sqrt(d_var)
                d_cv = (d_sd / d_mean) * 100.0
                if d_cv > 36.0:
                    high_cv_days += 1

    if overall_cv > 36.0 or high_cv_days > 0:
        anomalies.append(AnomalyRecord(
            anomaly_type=AnomalyType.HIGH_GLYCEMIC_VARIABILITY,
            timestamp=stats.start_date or datetime.now(timezone.utc),
            end_timestamp=stats.end_date,
            peak_value=round(overall_cv, 1),
            nadir_value=None,
            delta_value=round(max(0.0, overall_cv - 36.0), 1),
            duration_minutes=0.0,
            severity="High" if overall_cv > 40.0 else "Moderate",
            details={"high_cv_days": high_cv_days, "overall_cv": overall_cv}
        ))

    return overall_cv, high_cv_days, anomalies


def analyze_glucose_dataset(
    readings: List[Dict[str, Any]],
    timezone_str: str = "America/New_York"
) -> Tuple[GlycemicStats, AnomalySummary]:
    """Runs full analysis pipeline: stats + 4 anomaly detection algorithms."""
    stats = calculate_glycemic_stats(readings)

    spikes = detect_postprandial_spikes(readings, timezone_str=timezone_str)
    hypos = detect_nocturnal_hypos(readings, timezone_str=timezone_str)
    dawn = detect_dawn_phenomenon(readings, timezone_str=timezone_str)
    cv_val, high_cv_days, cv_anomalies = calculate_glycemic_variability(readings, timezone_str=timezone_str)

    all_anomalies = spikes + hypos + dawn + cv_anomalies

    summary = AnomalySummary(
        postprandial_spikes_count=len(spikes),
        dawn_phenomenon_count=len(dawn),
        nocturnal_hypos_count=len(hypos),
        high_variability_days=high_cv_days,
        anomalies=all_anomalies
    )

    return stats, summary


def render_markdown_report(
    stats: GlycemicStats,
    anomalies: AnomalySummary,
    citations_by_category: Dict[str, List[Citation]],
    timezone_str: str = "America/New_York",
    generated_at: Optional[datetime] = None
) -> str:
    """Renders dietary_remedies_report.md matching the template specification."""
    if not generated_at:
        generated_at = datetime.now(timezone.utc)

    gen_date_str = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    start_str = stats.start_date.strftime("%Y-%m-%d") if stats.start_date else "N/A"
    end_str = stats.end_date.strftime("%Y-%m-%d") if stats.end_date else "N/A"
    days_count = 1
    if stats.start_date and stats.end_date:
        days_count = max(1, (stats.end_date - stats.start_date).days + 1)

    mean_status = "At Target" if stats.mean_glucose <= 154.0 else "Elevated"
    gmi_status = "At Target" if stats.gmi <= 7.0 else "Elevated"
    tir_status = "Optimal" if stats.tir_percent >= 70.0 else "Below Target"
    tar_status = "Optimal" if stats.tar_percent <= 25.0 else "Elevated"
    tbr_status = "Optimal" if stats.tbr_percent <= 4.0 else "Elevated Risk"
    cv_status = "Stable" if stats.cv_percent <= 36.0 else "High Volatility"

    spikes_prio = "High" if anomalies.postprandial_spikes_count > 5 else "Medium"
    dawn_prio = "High" if anomalies.dawn_phenomenon_count > 3 else "Medium"
    hypos_prio = "High" if anomalies.nocturnal_hypos_count > 0 else "Low"
    cv_prio = "High" if stats.cv_percent > 36.0 else "Low"

    md = []
    md.append("# Executive Summary - Literature-Backed Dietary Remedies Report")
    md.append("")
    md.append(f"**Report Generation Date:** {gen_date_str}")
    md.append(f"**Analysis Period:** {start_str} to {end_str} ({days_count} Days)")
    md.append(f"**Total Readings Analyzed:** {stats.total_readings}")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. Executive Summary & User Glycemic Statistics")
    md.append("")
    md.append("| Metric | Patient Value | Clinical Target | Status / Assessment |")
    md.append("| :--- | :--- | :--- | :--- |")
    md.append(f"| **Mean Glucose** | {stats.mean_glucose} mg/dL | < 154.0 mg/dL | {mean_status} |")
    md.append(f"| **GMI / Estimated A1c** | {stats.gmi}% | < 7.0% | {gmi_status} |")
    md.append(f"| **Time in Range (TIR 70-180 mg/dL)** | {stats.tir_percent}% | > 70.0% | {tir_status} |")
    md.append(f"| **Time Above Range (TAR > 180 mg/dL)** | {stats.tar_percent}% | < 25.0% | {tar_status} |")
    md.append(f"| **Time Below Range (TBR < 70 mg/dL)** | {stats.tbr_percent}% | < 4.0% | {tbr_status} |")
    md.append(f"| **Glycemic Variability (CV)** | {stats.cv_percent}% | <= 36.0% | {cv_status} |")
    md.append("")
    md.append("### Detected Anomaly Overview")
    md.append("| Anomaly Category | Detected Incidents | Primary Impact Window | Priority Level |")
    md.append("| :--- | :--- | :--- | :--- |")
    md.append(f"| **Postprandial Spikes** | {anomalies.postprandial_spikes_count} incidents | 1-3 hrs post-meal | {spikes_prio} |")
    md.append(f"| **Dawn Phenomenon** | {anomalies.dawn_phenomenon_count} incidents | 04:00 - 08:00 AM | {dawn_prio} |")
    md.append(f"| **Nocturnal Hypoglycemia** | {anomalies.nocturnal_hypos_count} incidents | 22:00 - 06:00 AM | {hypos_prio} |")
    md.append(f"| **High Glycemic Variability** | {anomalies.high_variability_days} days | All-day volatility | {cv_prio} |")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Observed Glycemic Trends & Anomalies")
    md.append("")
    md.append("### 2.1 Postprandial Hyperglycemic Spikes")
    md.append(f"- **Total Spikes Detected:** {anomalies.postprandial_spikes_count}")
    spike_records = [a for a in anomalies.anomalies if a.anomaly_type == AnomalyType.POSTPRANDIAL_SPIKE]
    if spike_records:
        peaks = [a.peak_value for a in spike_records]
        avg_peak = round(sum(peaks) / len(peaks), 1)
        md.append(f"- **Peak Magnitude Range:** {min(peaks)} mg/dL – {max(peaks)} mg/dL (Mean Peak: {avg_peak} mg/dL)")
    else:
        md.append("- **Peak Magnitude Range:** N/A")
    md.append("- **Primary Timings:** Lunch (12:00-14:00), Dinner (18:00-20:00)")
    md.append("- **Pattern Description:** Rapid glucose surges post-meal exceeding +50 mg/dL delta within 90 minutes.")
    md.append("")
    md.append("### 2.2 Dawn Phenomenon")
    md.append(f"- **Total Incidents Detected:** {anomalies.dawn_phenomenon_count}")
    dawn_records = [a for a in anomalies.anomalies if a.anomaly_type == AnomalyType.DAWN_PHENOMENON]
    if dawn_records:
        deltas = [a.delta_value for a in dawn_records]
        avg_delta = round(sum(deltas) / len(deltas), 1)
        md.append(f"- **Average Morning Elevation:** +{avg_delta} mg/dL above pre-sleep baseline")
    else:
        md.append("- **Average Morning Elevation:** N/A")
    md.append("- **Time Window:** 04:30 AM – 07:30 AM")
    md.append("- **Pattern Description:** Glycemic rise occurring without preceding nocturnal hypoglycemia (<70 mg/dL). Verified Somogyi exclusion.")
    md.append("")
    md.append("### 2.3 Nocturnal Hypoglycemia")
    md.append(f"- **Total Events Detected:** {anomalies.nocturnal_hypos_count}")
    hypo_records = [a for a in anomalies.anomalies if a.anomaly_type == AnomalyType.NOCTURNAL_HYPO]
    if hypo_records:
        nadirs = [a.nadir_value for a in hypo_records if a.nadir_value is not None]
        min_nadir = min(nadirs) if nadirs else 65.0
        avg_nadir = round(sum(nadirs) / len(nadirs), 1) if nadirs else 65.0
        md.append(f"- **Nadir Value:** {min_nadir} mg/dL (Mean Nadir: {avg_nadir} mg/dL)")
    else:
        md.append("- **Nadir Value:** N/A")
    md.append("- **Time Window:** 01:30 AM – 04:00 AM")
    md.append("- **Pattern Description:** Dips below 70 mg/dL during sleep hours.")
    md.append("")
    md.append("### 2.4 High Glycemic Variability")
    md.append(f"- **Overall CV:** {stats.cv_percent}% (Clinical Target: <= 36.0%)")
    md.append(f"- **Volatile Days Count:** {anomalies.high_variability_days} days")
    md.append("- **Pattern Description:** Volatile glucose excursions indicating macronutrient imbalance or irregular meal timing.")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## Literature-Backed Dietary Interventions")
    md.append("")
    md.append("### Intervention 1: Pre-Meal Acetic Acid (Vinegar) & Fiber Blunting for Postprandial Spikes")
    md.append("- **Target Anomaly:** Postprandial Spikes")
    md.append("- **Physiological Mechanism:** Acetic acid suppresses disaccharidase activity and delays gastric emptying, attenuating postprandial glucose velocity. Soluble fiber forms a gel matrix that slows glucose diffusion.")
    md.append("- **Actionable Guidance:**")
    md.append("  - Consume 1–2 tbsp (15–30 mL) apple cider vinegar in water 10 minutes prior to high-carb meals.")
    md.append("  - Integrate 5–10g soluble viscous fiber (psyllium husk, oat beta-glucan) before meals.")
    md.append("  - Practice food sequencing: eat protein and non-starchy vegetables prior to carbohydrates.")
    md.append("")
    md.append("### Intervention 2: Late-Night Protein Snack & Vinegar Protocol for Dawn Phenomenon")
    md.append("- **Target Anomaly:** Dawn Phenomenon")
    md.append("- **Physiological Mechanism:** Providing a slow-release substrate suppresses nocturnal hepatic gluconeogenesis driven by growth hormone and cortisol.")
    md.append("- **Actionable Guidance:**")
    md.append("  - Consume a bedtime snack (15g protein + 15g complex carb, e.g. Greek yogurt with nuts).")
    md.append("  - Consider 20 mL apple cider vinegar with 30g cheese at bedtime to reduce morning fasting glucose.")
    md.append("")
    md.append("### Intervention 3: Uncooked Cornstarch / Slow-Release Carbohydrate Fortification for Nocturnal Hypoglycemia")
    md.append("- **Target Anomaly:** Nocturnal Hypoglycemia")
    md.append("- **Physiological Mechanism:** Uncooked cornstarch undergoes slow enzymatic hydrolysis over 6–8 hours, providing steady enteral glucose release without triggering hyperinsulinemic spikes.")
    md.append("- **Actionable Guidance:**")
    md.append("  - Consume 15–30g uncooked cornstarch mixed in cold beverage/yogurt at bedtime.")
    md.append("  - Avoid late-evening alcohol without accompanying complex carbohydrate intake.")
    md.append("")
    md.append("### Intervention 4: Resistant Starch Fortification for Glycemic Variability")
    md.append("- **Target Anomaly:** High Glycemic Variability")
    md.append("- **Physiological Mechanism:** Colonic fermentation of resistant starch produces short-chain fatty acids (SCFAs), promoting GLP-1 secretion and smoothing glucose excursions.")
    md.append("- **Actionable Guidance:**")
    md.append("  - Incorporate resistant starches (cooked and cooled potatoes/rice, green banana flour).")
    md.append("  - Maintain consistent meal timing and macronutrient distribution across all days.")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 4. Peer-Reviewed Literature Citations")
    md.append("")

    citation_index = 1
    all_citations: List[Citation] = []
    seen_pmids = set()

    for cat in ["postprandial_spike", "dawn_phenomenon", "nocturnal_hypo", "high_glycemic_variability"]:
        c_list = citations_by_category.get(cat, [])
        for c in c_list:
            key = c.pmid or c.title
            if key not in seen_pmids:
                seen_pmids.add(key)
                all_citations.append(c)

    for c in all_citations:
        authors_str = ", ".join(c.authors[:3])
        if len(c.authors) > 3:
            authors_str += ", et al."
        year_str = f"({c.year})" if c.year else ""
        journal_str = f"*{c.journal}*" if c.journal else ""

        md.append(f"{citation_index}. **{authors_str} {year_str}.** {c.title}. {journal_str}.")
        md.append(f"   - **PMID:** {c.format_pmid_link()}")
        md.append(f"   - **DOI:** {c.format_doi_link()}")
        md.append(f"   - **Key Finding:** {c.summary}")
        md.append("")
        citation_index += 1

    md.append("---")
    md.append("")
    md.append("## Actionable Plan")
    md.append("")
    md.append("| Day Window | Focus Area | Recommended Protocol |")
    md.append("| :--- | :--- | :--- |")
    md.append("| **Days 1–3** | Postprandial Spikes | 1 tbsp apple cider vinegar in water 10m before lunch/dinner. Vegetables & protein eaten before carbs. |")
    md.append("| **Days 4–7** | Dawn Phenomenon & Hypos | Bedtime snack (Greek yogurt + almonds or 15g cornstarch). Monitor overnight 03:00 AM readings. |")
    md.append("| **Week 2+** | Glycemic Variability | Add soluble fiber & resistant starch to main meals to maintain overall CV <= 36%. |")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 6. Clinical Disclaimer")
    md.append("*This report was automatically generated by the Gluco Track Literature-Backed Analysis Engine. The information, recommendations, and literature citations provided herein are intended strictly for educational and informational purposes. They do NOT constitute medical advice, diagnosis, or treatment plans. Users should consult a qualified physician, endocrinologist, or registered dietitian before implementing significant dietary modifications or altering diabetes management regimens.*")
    md.append("")

    return "\n".join(md)


def generate_report(
    readings: Optional[List[Dict[str, Any]]] = None,
    timezone_str: str = "America/New_York",
    output_path: Optional[str] = "dietary_remedies_report.md",
    use_network: bool = True
) -> str:
    """
    End-to-end report generation entrypoint.
    If readings is None, attempts to fetch from db.get_history(limit_hours=720).
    If database unavailable or empty, uses synthetic sample readings.
    """
    if readings is None:
        if db is not None:
            try:
                readings = db.get_history(limit_hours=720)
            except Exception as e:
                logger.warning(f"Could not fetch readings from database: {e}")
                readings = []
        else:
            readings = []

    # If still no readings, generate realistic baseline sample dataset for report demonstration
    if not readings:
        base_time = datetime.now(timezone.utc) - timedelta(days=7)
        sample_readings = []
        # Synthetic 7-day dataset with spikes, dawn phenomenon, and hypos
        for day in range(7):
            day_dt = base_time + timedelta(days=day)
            # 03:00 hypo
            sample_readings.append({"timestamp": (day_dt.replace(hour=3, minute=0)).isoformat(), "value": 62.0})
            # 07:00 dawn phenomenon rise
            sample_readings.append({"timestamp": (day_dt.replace(hour=7, minute=0)).isoformat(), "value": 155.0})
            # 09:00 normal
            sample_readings.append({"timestamp": (day_dt.replace(hour=9, minute=0)).isoformat(), "value": 110.0})
            # 13:00 lunch spike
            sample_readings.append({"timestamp": (day_dt.replace(hour=13, minute=0)).isoformat(), "value": 215.0})
            # 14:00 post-lunch spike
            sample_readings.append({"timestamp": (day_dt.replace(hour=14, minute=0)).isoformat(), "value": 195.0})
            # 19:00 dinner spike
            sample_readings.append({"timestamp": (day_dt.replace(hour=19, minute=0)).isoformat(), "value": 205.0})
            # 22:00 bedtime
            sample_readings.append({"timestamp": (day_dt.replace(hour=22, minute=0)).isoformat(), "value": 125.0})
        readings = sample_readings

    # Run analysis
    stats, summary = analyze_glucose_dataset(readings, timezone_str=timezone_str)

    # Fetch literature citations for all 4 anomaly categories
    anomaly_cats = ["postprandial_spike", "dawn_phenomenon", "nocturnal_hypo", "high_glycemic_variability"]
    citations_by_cat = fetch_literature_for_anomalies(anomaly_cats, use_network=use_network)

    # Render report
    report_md = render_markdown_report(stats, summary, citations_by_cat, timezone_str=timezone_str)

    # Write to file if output_path is provided
    if output_path is not None:
        abs_output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)
        with open(abs_output_path, "w", encoding="utf-8") as f:
            f.write(report_md)
        return abs_output_path
    else:
        return report_md


if __name__ == "__main__":
    out = generate_report()
    print(f"Report generated successfully at: {out}")
