type Id<Tag extends string> = string & { readonly __idType: Tag };

export type MapId = Id<"Map">;
export type BuildingId = Id<"Building">;
export type FloorId = Id<"Floor">;
export type PlaceId = Id<"Place">;
export type AccessPointId = Id<"AccessPoint">;
export type NodeId = Id<"Node">;
export type SegmentId = Id<"Segment">;
export type EdgeId = Id<"Edge">;
export type AccessLinkId = Id<"AccessLink">;
export type ConnectorId = Id<"Connector">;

export const asMapId = (value: string): MapId => value as MapId;
export const asBuildingId = (value: string): BuildingId => value as BuildingId;
export const asFloorId = (value: string): FloorId => value as FloorId;
export const asPlaceId = (value: string): PlaceId => value as PlaceId;
export const asAccessPointId = (value: string): AccessPointId => value as AccessPointId;
export const asNodeId = (value: string): NodeId => value as NodeId;
export const asSegmentId = (value: string): SegmentId => value as SegmentId;
export const asEdgeId = (value: string): EdgeId => value as EdgeId;
export const asAccessLinkId = (value: string): AccessLinkId => value as AccessLinkId;
export const asConnectorId = (value: string): ConnectorId => value as ConnectorId;
