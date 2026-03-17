interface ToggleSwitchProps {
  checked?: boolean;
  enabled?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  onToggle?: () => void;
  activeColor?: string;
  disabled?: boolean;
  'aria-label'?: string;
}

export default function ToggleSwitch({
  checked,
  enabled,
  onCheckedChange,
  onToggle,
  activeColor = '#2563eb',
  disabled = false,
  'aria-label': ariaLabel,
}: ToggleSwitchProps) {
  const isChecked = checked ?? enabled ?? false;

  const handleToggle = () => {
    if (disabled) return;
    onCheckedChange?.(!isChecked);
    onToggle?.();
  };

  return (
    <button
      type="button"
      onClick={handleToggle}
      disabled={disabled}
      role="switch"
      aria-checked={isChecked}
      aria-label={ariaLabel}
      className="relative h-6 w-10 shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      style={{ background: isChecked ? activeColor : '#d1d5db' }}
    >
      <span
        className={`absolute left-[3px] top-[3px] h-[18px] w-[18px] rounded-full bg-white shadow-sm transition-transform ${
          isChecked ? 'translate-x-[16px]' : 'translate-x-0'
        }`}
      />
    </button>
  );
}
