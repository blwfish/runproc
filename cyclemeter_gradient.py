#!/usr/bin/env python3
"""
cyclemeter_gradient.py — inject a gradient chart into a Cyclemeter activity page

Usage:
    python3 cyclemeter_gradient.py <cyclemeter-url> [-o output.html]

Fetches the page and its KML track, computes grade every 0.05 miles,
injects a colour-coded gradient bar chart + compact elevation profile
before </body>, and saves a self-contained local HTML file.
"""

import sys
import re
import math
import json
import argparse
import urllib.request
from pathlib import Path


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def parse_kml_coords(kml_text):
    blocks = re.findall(r"<coordinates>(.*?)</coordinates>", kml_text, re.DOTALL)
    # Collect all non-empty blocks; they are contiguous track segments stored in order
    all_points = []
    for block in blocks:
        toks = [t for t in block.split() if t.count(",") == 2]
        if toks:
            pts = [tuple(map(float, t.split(","))) for t in toks]
            # Skip the first point of subsequent segments — it duplicates the previous end
            start = 1 if all_points and pts[0] == all_points[-1] else 0
            all_points.extend(pts[start:])
    return all_points  # (lon, lat, ele_m)


def haversine_mi(p1, p2):
    R = 3958.8
    dlat = math.radians(p2[1] - p1[1])
    dlon = math.radians(p2[0] - p1[0])
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(p1[1])) * math.cos(math.radians(p2[1])) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_data(points, seg=0.05, smooth_window=10, elev_points=300):
    raw = [(0.0, points[0][2])]
    cum = 0.0
    for i in range(1, len(points)):
        cum += haversine_mi(points[i - 1], points[i])
        raw.append((cum, points[i][2]))

    sm = []
    for i, (d, e) in enumerate(raw):
        window = raw[max(0, i - smooth_window): i + smooth_window + 1]
        sm.append((d, sum(x[1] for x in window) / len(window)))

    total = raw[-1][0]
    gradients = []
    d = 0.0
    while d + seg <= total:
        before = next((p for p in sm if p[0] >= d), sm[0])
        after = next((p for p in sm if p[0] >= d + seg), sm[-1])
        rise_ft = (after[1] - before[1]) * 3.28084
        run_ft = seg * 5280
        gradients.append({"d": round(d + seg / 2, 3), "g": round(rise_ft / run_ft * 100, 1)})
        d += seg / 2

    step = max(1, len(sm) // elev_points)
    elev = [{"d": round(sm[i][0], 3), "e": round(sm[i][1] * 3.28084, 1)} for i in range(0, len(sm), step)]

    return {
        "grad": gradients,
        "elev": elev,
        "total_mi": round(total, 3),
        "ele_min_ft": round(min(p[2] for p in points) * 3.28084, 1),
        "ele_max_ft": round(max(p[2] for p in points) * 3.28084, 1),
    }


CHART_JS = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"

INJECTION_TEMPLATE = """\
<div id="cm-gradient-section" style="font-family:sans-serif;padding:10px 16px 14px;border-top:1px solid #ddd;">
  <div style="font-size:12px;color:#666;margin-bottom:6px;">
    Gradient &nbsp;·&nbsp; {total_mi} mi &nbsp;·&nbsp; {ele_min_ft}–{ele_max_ft} ft
    <span style="margin-left:14px;">
      <span style="display:inline-block;width:8px;height:8px;background:#639922;border-radius:1px;margin-right:3px;"></span>flat (≤2%)
      <span style="display:inline-block;width:8px;height:8px;background:#BA7517;border-radius:1px;margin:0 3px 0 10px;"></span>moderate (2–5%)
      <span style="display:inline-block;width:8px;height:8px;background:#D85A30;border-radius:1px;margin:0 3px 0 10px;"></span>hard (5–8%)
      <span style="display:inline-block;width:8px;height:8px;background:#E24B4A;border-radius:1px;margin:0 3px 0 10px;"></span>steep (&gt;8%)
    </span>
  </div>
  <div style="position:relative;height:110px;margin-bottom:4px;"><canvas id="cm-grad-canvas"></canvas></div>
  <div style="position:relative;height:70px;"><canvas id="cm-elev-canvas"></canvas></div>
</div>
<script src="{chart_js}"></script>
<script>
(function () {{
  var grad = {grad_json};
  var elev = {elev_json};

  function gradeColor(g) {{
    var a = Math.abs(g);
    if (a <= 2) return '#639922';
    if (a <= 5) return '#BA7517';
    if (a <= 8) return '#D85A30';
    return '#E24B4A';
  }}

  new Chart(document.getElementById('cm-grad-canvas'), {{
    type: 'bar',
    data: {{
      labels: grad.map(function (p) {{ return p.d; }}),
      datasets: [{{
        data: grad.map(function (p) {{ return p.g; }}),
        backgroundColor: grad.map(function (p) {{ return gradeColor(p.g); }}),
        borderWidth: 0,
        barPercentage: 1.0,
        categoryPercentage: 1.0
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            title: function (i) {{ return grad[i[0].dataIndex].d.toFixed(2) + ' mi'; }},
            label: function (c) {{ return (c.raw > 0 ? '+' : '') + c.raw + '%'; }}
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{
            autoSkip: false,
            maxRotation: 0,
            font: {{ size: 10 }},
            callback: function (v, i) {{
              var d = grad[i] && grad[i].d;
              return (d !== undefined && Math.abs(d - Math.round(d)) < 0.04) ? Math.round(d) + ' mi' : '';
            }}
          }},
          grid: {{ display: false }}
        }},
        y: {{
          min: -14, max: 14,
          ticks: {{ callback: function (v) {{ return v + '%'; }}, maxTicksLimit: 5, font: {{ size: 10 }} }},
          grid: {{ color: 'rgba(0,0,0,0.06)' }}
        }}
      }}
    }}
  }});

  new Chart(document.getElementById('cm-elev-canvas'), {{
    type: 'line',
    data: {{
      labels: elev.map(function (p) {{ return p.d; }}),
      datasets: [{{
        data: elev.map(function (p) {{ return p.e; }}),
        borderColor: '#378ADD',
        backgroundColor: 'rgba(55,138,221,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 1.5
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            title: function (i) {{ return elev[i[0].dataIndex].d.toFixed(2) + ' mi'; }},
            label: function (c) {{ return Math.round(c.raw) + ' ft'; }}
          }}
        }}
      }},
      scales: {{
        x: {{ display: false }},
        y: {{
          ticks: {{ callback: function (v) {{ return v + ' ft'; }}, maxTicksLimit: 3, font: {{ size: 10 }} }},
          grid: {{ color: 'rgba(0,0,0,0.06)' }}
        }}
      }}
    }}
  }});
}})();
</script>
"""


def inject(page_html, data):
    # Fix relative asset URLs so they resolve against cyclemeter.com
    if "<base " not in page_html:
        page_html = page_html.replace("<head>", "<head>\n<base href=\"https://cyclemeter.com/\">", 1)

    block = INJECTION_TEMPLATE.format(
        total_mi=data["total_mi"],
        ele_min_ft=data["ele_min_ft"],
        ele_max_ft=data["ele_max_ft"],
        chart_js=CHART_JS,
        grad_json=json.dumps(data["grad"]),
        elev_json=json.dumps(data["elev"]),
    )

    if "</body>" in page_html:
        page_html = page_html.replace("</body>", block + "\n</body>", 1)
    else:
        page_html += block

    return page_html


def main():
    parser = argparse.ArgumentParser(description="Add gradient chart to a Cyclemeter activity page")
    parser.add_argument("url", help="Cyclemeter activity URL")
    parser.add_argument("-o", "--output", help="Output file (default: gradient_<activity>.html)")
    args = parser.parse_args()

    url = args.url.rstrip("/")
    print(f"Fetching page …  {url}")
    page_html = fetch(url)

    kml_match = re.search(r"https://share\.abvio\.com/[^\"']+\.kml", page_html)
    if not kml_match:
        sys.exit("Error: KML link not found in page HTML")
    kml_url = kml_match.group(0)

    print(f"Fetching KML …   {kml_url}")
    kml_text = fetch(kml_url)

    points = parse_kml_coords(kml_text)
    print(f"Parsed {len(points)} GPS points")

    data = compute_data(points)
    print(f"Distance: {data['total_mi']} mi   Elevation: {data['ele_min_ft']}–{data['ele_max_ft']} ft")

    modified = inject(page_html, data)

    out = Path(args.output) if args.output else Path(f"gradient_{url.split('/')[-1]}.html")
    out.write_text(modified, encoding="utf-8")
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
