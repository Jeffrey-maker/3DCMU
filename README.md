# 3DCMU Indoor Navigation

This project connects 2D Scott Hall and Wean Hall floor plans into an indoor navigation graph. It includes room-to-room routing, cross-floor connectors, accessible routes, and an interactive phone-sized Three.js map.

## Run the 3D UI

```bash
cd campus_route_ui
npm install
python3 -m http.server 8766 --directory ..
```

Open `http://localhost:8766/campus_route_ui/`.

The 3D map supports rotation, zoom, floor filtering, animated routes, stairs, an elevator, and an escalator. Standard cross-floor routes recommend stairs. Accessible routes use the elevator. Escalators are displayed but are not recommended until their operating direction is confirmed.

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
