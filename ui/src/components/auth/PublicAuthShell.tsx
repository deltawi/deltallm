import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import BrandLogo from '../BrandLogo';

interface PublicAuthShellProps {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
}

export default function PublicAuthShell({ title, description, children, footer }: PublicAuthShellProps) {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <BrandLogo variant="mark" className="mb-4 justify-center" markClassName="h-16 w-16 rounded-2xl" />
          <h1 className="text-2xl font-bold text-gray-900">{title}</h1>
          <p className="text-gray-500 mt-2">{description}</p>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="p-6">{children}</div>
          <div className="border-t border-gray-200 bg-gray-50 px-6 py-4 text-sm text-gray-600">
            {footer ?? (
              <Link to="/login" className="font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover">
                Back to sign in
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
