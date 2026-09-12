# 3DCMU Indoor Navigation

This project connects 2D Scott Hall and Wean Hall floor plans into an indoor navigation graph. It includes room-to-room routing, cross-floor connectors, accessible routes, and an interactive phone-sized Three.js map.

## Run the 3D UI

```bash
cd campus_route_ui
npm install
python3 -m http.server 8766 --directory ..
```

Open `http://localhost:8766/campus_route_ui/`.

The 3D map loads all 1,262 labeled spaces extracted from the 16 supplied Scott Hall and Wean Hall plans. Every room is represented by a visible 3D block on every floor, including the full stacked-building view. Selecting one floor also displays a high-contrast room-number label at every mapped position. It supports rotation, zoom, floor filtering, animated routes, stairs, elevators, and escalators. For standard trips, the planner compares the connector alternatives that are reachable through the mapped floor walkspace and recommends the lowest-cost route. Accessible trips use elevators only.

Same-floor route segments are found with A* over a raster derived from the corresponding floor plan. A route fails closed when its room, connector, or bridge portal cannot be reached without crossing a detected wall. Floor changes can only use a modeled vertical connector, and every Scott-Wean trip is forced through the labeled Level 4 bridge portals.

## Regenerate floor data

Install the extraction dependency, then generate room coordinates and rendered floor images from the source PDF folders:

```bash
python3 -m pip install -r tools/requirements.txt
python3 tools/extract_floor_data.py /path/to/scott_floors /path/to/wean_floors campus_route_ui/floor-data.js
swift tools/render_floorplans.swift /path/to/scott_floors /path/to/wean_floors campus_route_ui/assets/floors
```

## Run Python examples

```bash
python3 cmu_route_examples.py
python3 -m unittest -v test_cmu_route_examples.py test_indoor_navigation.py
```

`indoor_navigation.py` provides the generic grid-based navigation API. `cmu_route_examples.py` contains the current Scott Hall and Wean Hall landmark graph.

## Verify the UI

With the local server running on port 8766:

```bash
cd campus_route_ui
npm install
node verify.mjs
```

The verifier uses the locally installed Google Chrome application and checks phone and desktop layouts, route recommendations, accessibility instructions, and the WebGL canvas.
