# Contour / 3DCMU

A visual TypeScript and React workspace for turning building floorplans into an
indoor routing graph.

## Run the editor

```sh
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## Authoring workflow

1. Import several PNG, JPG, WebP, or PDF floorplans at once. Every page in a
   multi-page PDF becomes a floor.
2. Choose a building from the left rail, or add another building with **+**.
   Floorplans are imported into the selected building. Rename the building,
   floor, and level in the inspector.
3. Use **Hallway** to click or reuse endpoints. Every drawn path starts with two
   directed edges whose heading, instruction, type, and cost can be edited.
4. Use **Room** to place a destination, then click all reachable hallway nodes.
   These dashed terminal links can only begin or end a route; they can never be
   used as a shortcut through a room.
5. Place a stair landing or elevator lobby. Its marker is already a routing
   node: use **Start hallway from this stop** in the inspector (or click the
   marker with the Hallway tool), then click the corridor node. Do not add a
   duplicate node over the connector. Stops representing the same physical
   connector can then be merged into one named group across floors.
6. To join neighboring buildings, choose **Connector**, click the portal point
   on one floorplan, switch to the other building and floor, and click the other
   portal point. Contour creates a two-way connector hallway and marks both
   portal endpoints on their respective sheets. A portal marker is already a
   routing node: with **Hallway** active, click the portal marker and then the
   local hallway node. You can also select the portal and choose **Start hallway
   from this portal** in the inspector.
7. Export a self-contained `.contour.json` project. It includes the rendered
   floorplan images and can be reopened from the header.

Floor and building settings include a **Remove data** section. Removal first
shows the number of floorplans, nodes, paths, and places affected. Removing a
floor also cleans up its access links, connector stops, and paths entering that
floor. Removing a building does the same for all of its floors, including
connector hallways from neighboring buildings. A project always retains at
least one building.

## Navigation mode

Open a project and choose **Navigate** in the header. Select any two annotated
places and choose **Find route**. The navigator:

- honors directed hallway edges and their costs;
- uses terminal room links only for departure and arrival;
- generates between-floor travel from grouped stair/elevator stops;
- traverses directed connector hallways between neighboring buildings;
- highlights only the active route on each floorplan; and
- provides building-aware sheet tabs and ordered instructions for cross-floor
  and cross-building routes.

Enable **Step-free route** before finding a route to exclude stair connectors,
stair path segments, and edges explicitly marked as wheelchair-inaccessible.
Elevators remain available. Edges whose accessibility is unknown are retained
until they are annotated more precisely.

Costs are relative authoring distances, not estimated walking time. Cross-floor
routing requires every connector stop to be attached to its floor's hallway
graph and grouped with the matching stop on the other floor.

Edits autosave to IndexedDB in the current browser. The SVG annotation layer
uses coordinates from the original rendered plan, so annotations remain stable
while zooming, panning, or resizing the window.

## Data model

- `Place`: searchable rooms, stairs, elevators, and landmarks.
- `RoutingNode`: traversable hallway points and vertical-connector landings.
- `PathSegment`: geometry drawn once between two nodes.
- `DirectedEdge`: one permitted direction and its navigation instruction.
- `TerminalAccessLink`: endpoint-only room access from a hallway node.
- `VerticalConnector`: a stairway or elevator grouping stops across floors.
- `building_connection`: a directed hallway transition between portal nodes in
  different buildings.

The schema is in `src/model/topology.ts`; referential validation is in
`src/model/validation.ts`.

## Checks

```sh
npm test
npm run build
```
