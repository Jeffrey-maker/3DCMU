import type { DirectionStep } from "../api/client";

interface DirectionsPanelProps {
  steps: DirectionStep[];
  phrasedText: string | null;
  currentStepIndex: number;
  onStepSelect: (index: number) => void;
}

export function DirectionsPanel({
  steps,
  phrasedText,
  currentStepIndex,
  onStepSelect,
}: DirectionsPanelProps) {
  return (
    <div className="directions-panel">
      {phrasedText && (
        <div className="phrased-directions">
          <h4>Directions (K2 Horizon)</h4>
          <p>{phrasedText}</p>
        </div>
      )}
      <h4>Step-by-step</h4>
      <ol>
        {steps.map((step, i) => (
          <li
            key={`${step.node_id}-${i}`}
            className={i === currentStepIndex ? "current-step" : ""}
            onClick={() => onStepSelect(i)}
          >
            {step.text}
          </li>
        ))}
      </ol>
    </div>
  );
}
