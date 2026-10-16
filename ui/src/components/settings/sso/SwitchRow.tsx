import ToggleSwitch from '../../ToggleSwitch';

/** One settings switch with a title and a description. `highlight` draws an amber warning box. */
export default function SwitchRow({ title, description, checked, onChange, highlight = false }: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  highlight?: boolean;
}) {
  return (
    <div className={`flex items-start justify-between gap-4 rounded-lg px-3 py-3 ${highlight ? 'border border-amber-200 bg-amber-50/60' : ''}`}>
      <div>
        <p className="text-sm font-medium text-gray-800">{title}</p>
        <p className="mt-0.5 text-xs text-gray-500">{description}</p>
      </div>
      <ToggleSwitch checked={checked} onCheckedChange={onChange} aria-label={title} />
    </div>
  );
}
