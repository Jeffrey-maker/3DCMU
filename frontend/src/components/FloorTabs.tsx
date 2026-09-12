// Degenerately one tab for v1 (Wean Hall Level 1 only), but structurally
// ready for milestone 5's multi-floor buildings.

interface FloorTabsProps {
  floors: number[];
  selectedFloor: number;
  onSelectFloor: (floor: number) => void;
}

export function FloorTabs({ floors, selectedFloor, onSelectFloor }: FloorTabsProps) {
  return (
    <div className="floor-tabs">
      {floors.map((floor) => (
        <button
          key={floor}
          type="button"
          className={floor === selectedFloor ? "active" : ""}
          onClick={() => onSelectFloor(floor)}
        >
          Floor {floor}
        </button>
      ))}
    </div>
  );
}
