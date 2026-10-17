import type { ButtonHTMLAttributes, ReactNode } from 'react';
import clsx from 'clsx';

export type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'success' | 'danger' | 'danger-outline' | 'warning-outline' | 'ghost' | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  fullWidth?: boolean;
  children?: ReactNode;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary: 'border-transparent bg-brand-primary text-brand-on-primary hover:bg-brand-primary-hover',
  secondary: 'border-brand-secondary bg-white text-brand-secondary-ink hover:bg-brand-secondary-soft',
  outline: 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50 hover:text-gray-900',
  success: 'border-transparent bg-emerald-600 text-white hover:bg-emerald-700',
  danger: 'border-transparent bg-red-600 text-white hover:bg-red-700',
  'danger-outline': 'border-red-200 bg-white text-red-600 hover:bg-red-50',
  'warning-outline': 'border-amber-200 bg-white text-amber-700 hover:bg-amber-50',
  ghost: 'border-transparent bg-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-900',
  link: 'border-transparent bg-transparent text-brand-primary-ink hover:text-brand-primary-ink-hover',
};

// Heights come from the control-* classes in index.css: sm 28 px, md 32 px, lg 40 px (8 px taller on phones).
// lg is only for the sign-in, password and MFA pages.
const sizeClasses: Record<ButtonSize, string> = {
  sm: 'control-sm gap-1.5 rounded-md',
  md: 'control-md gap-1.5 rounded-lg',
  lg: 'control-lg gap-2 rounded-lg',
  // Same height as md, so icon-only buttons line up with labelled ones.
  icon: 'control-icon-md rounded-lg',
  'icon-sm': 'control-icon-sm rounded-md',
};

export default function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  fullWidth = false,
  disabled,
  className,
  children,
  type = 'button',
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        'inline-flex items-center justify-center border font-medium shadow-sm transition-colors',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        variantClasses[variant],
        sizeClasses[size],
        fullWidth && 'w-full',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
