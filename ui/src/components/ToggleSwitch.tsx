interface ToggleSwitchProps {
  enabled: boolean;
  onToggle: () => void;
  activeColor?: string;
  disabled?: boolean;
  'aria-label'?: string;
}

export default function ToggleSwitch({
  enabled,
  onToggle,
  activeColor = '#2563eb',
  disabled = false,
  'aria-label': ariaLabel,
}: ToggleSwitchProps) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onToggle}
      disabled={disabled}
      role="switch"
      aria-checked={enabled}
      aria-label={ariaLabel}
      className="relative rounded-full transition-colors shrink-0 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:opacity-50 disabled:cursor-not-allowed"
      style={{ height: 22, width: 40, background: enabled ? activeColor : '#d1d5db' }}
    >
      <span
        className={`absolute left-0 top-[2px] w-[18px] h-[18px] bg-white rounded-full shadow transition-transform ${
          enabled ? 'translate-x-[20px]' : 'translate-x-[2px]'
        }`}
      />
    </button>
  );
}
