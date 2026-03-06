interface JourneyStep {
  label: string;
  done: boolean;
  hint?: string;
}

interface JourneyChecklistProps {
  title: string;
  description: string;
  steps: JourneyStep[];
}

export default function JourneyChecklist({ title, description, steps }: JourneyChecklistProps) {
  const gridClass =
    steps.length >= 4 ? 'mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4' : 'mt-4 grid gap-3 sm:grid-cols-3';

  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:p-5">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          <p className="mt-1 text-sm text-slate-600">{description}</p>
        </div>
        <div className="text-xs font-medium text-slate-500">
          {steps.filter((step) => step.done).length}/{steps.length} complete
        </div>
      </div>
      <div className={gridClass}>
        {steps.map((step, index) => (
          <div
            key={step.label}
            className={`rounded-xl border px-3 py-3 ${
              step.done ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-slate-200 bg-white text-slate-800'
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${
                  step.done ? 'bg-emerald-600 text-white' : 'bg-slate-200 text-slate-700'
                }`}
              >
                {step.done ? '✓' : index + 1}
              </span>
              <span className="text-sm font-medium">{step.label}</span>
            </div>
            {step.hint && <p className="mt-2 text-xs text-slate-500">{step.hint}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
