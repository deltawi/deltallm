import type { ReactNode } from 'react';

type HeroTabbedDetailShellProps = {
  backBar: ReactNode;
  hero: ReactNode;
  body: ReactNode;
  layout?: 'full_bleed' | 'contained';
  containerClassName?: string;
};

export default function HeroTabbedDetailShell({
  backBar,
  hero,
  body,
  layout = 'full_bleed',
  containerClassName = 'mx-auto max-w-7xl space-y-5 px-6 pt-6',
}: HeroTabbedDetailShellProps) {
  if (layout === 'contained') {
    return (
      <div className="min-h-screen bg-gray-50">
        <div className="border-b border-gray-200 bg-white px-6 py-3">{backBar}</div>
        <div className={containerClassName}>
          {hero}
          {body}
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="border-b border-gray-200 bg-white px-6 py-3">{backBar}</div>
      {hero}
      <div className="px-6 pb-8 pt-0">{body}</div>
    </div>
  );
}
