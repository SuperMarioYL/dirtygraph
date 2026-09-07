"""Render SVG explanations and site transcripts from docs/demo-results.json."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
result = json.loads((ROOT / 'docs/demo-results.json').read_text())
assets = ROOT / 'assets'
web_assets = ROOT / 'web/assets'
web_assets.mkdir(parents=True, exist_ok=True)
positions = {name: (32 + (i % 4) * 184, 54 if i < 4 else 168)
             for i, name in enumerate(result['nodes'])}


def svg(stage, dark=False, animated=False):
    bg, ink, muted, line = ('#1d211d', '#f0f1e9', '#a9afa2', '#485044') if dark else ('#faf9f6', '#252720', '#676b60', '#d9ddd2')
    accent, tint = ('#e6b365', '#443623') if dark else ('#945711', '#f4e4c7')
    green, green_tint = ('#a3c89b', '#2a3a2a') if dark else ('#38653d', '#e4edde')
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 266" role="img" aria-labelledby="title desc"><title id="title">DirtyGraph: eight-node example</title><desc id="desc">Editing auth affects auth, session, views and api. Invoice, billing, search and logger are unchanged. Arrows show change propagation.</desc><rect width="760" height="266" fill="{bg}"/>',
             f'<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 1L7 4L0 7" fill="none" stroke="{muted}" stroke-width="1.4"/></marker></defs>',
             '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}.flow{stroke-dasharray:6 5;animation:flow 3s linear infinite}@keyframes flow{to{stroke-dashoffset:-44}}@media(prefers-reduced-motion:reduce){.flow{animation:none;stroke-dasharray:none}}</style>',
             f'<text x="32" y="28" fill="{muted}" font-size="16">CHANGE PROPAGATION</text>',
             f'<text x="32" y="149" fill="{muted}" font-size="16">UNCHANGED</text>']
    for dependent, dependency in result['dependencies']:
        x1,y1=positions[dependency];x2,y2=positions[dependent]
        css = ' class="flow"' if animated and dependency in result['affected'] else ''
        color=accent if stage==1 and dependency in result['affected'] else muted
        parts.append(f'<path{css} d="M{x1+144} {y1+27} H{x2-10}" fill="none" stroke="{color}" stroke-width="1.8" marker-end="url(#arrow)"/>')
    for name,(x,y) in positions.items():
        active=name in result['affected']
        fill, stroke, state=bg,line,'clean'
        if stage==1 and active:fill,stroke,state=tint,accent,'changed' if name=='auth' else 'affected'
        if stage==2 and active:fill,stroke,state=green_tint,green,'re-derived'
        color=stroke if active and stage in (1,2) else muted
        parts += [f'<rect x="{x}" y="{y}" width="144" height="54" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
                  f'<text x="{x+72}" y="{y+35}" text-anchor="middle" fill="{ink}" font-size="25" font-weight="500">{name}</text>',
                  f'<text x="{x+72}" y="{y+76}" text-anchor="middle" fill="{color}" font-size="17">{state}</text>']
    parts.append('</svg>')
    return ''.join(parts)


for stage in range(4):
    for theme in ['light', 'dark']:
        name=f'demo-{stage}-{theme}.svg'
        (web_assets/name).write_text(svg(stage, theme=='dark'))
for theme in ['light', 'dark']:
    (assets/f'flow-{theme}.svg').write_text(svg(1, theme=='dark', animated=True))
    bg,ink=('#171a17','#f0f1e9') if theme=='dark' else ('#ffffff','#252720')
    (assets/f'hero-{theme}.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 880 125" role="img" aria-labelledby="t"><title id="t">DirtyGraph</title><rect width="880" height="125" fill="{bg}"/><path d="M34 37H63V87H92" stroke="#b77829" stroke-width="4" fill="none"/><g fill="#b77829"><circle cx="34" cy="37" r="7"/><circle cx="63" cy="62" r="7"/><circle cx="92" cy="87" r="7"/></g><text x="121" y="86" fill="{ink}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif" font-size="62" font-weight="600" letter-spacing="-2">DirtyGraph</text></svg>')
site_path = ROOT / 'web/site.json'
site = json.loads(site_path.read_text())
for i, step in enumerate(site['demo']['steps']):
    step.update(result['steps'][i])
site['meta'] = {'content_version': result['version'], 'demo_source': 'docs/demo-results.json'}
site_path.write_text(json.dumps(site, ensure_ascii=False, indent=2) + '\n')
