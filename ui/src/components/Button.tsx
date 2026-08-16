import type { ButtonHTMLAttributes, ReactNode } from 'react';
import clsx from 'clsx';

export type ButtonVariant = 'primary' | 'secondary' | 'success' | 'danger' | 'ghost' | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

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
  success: 'border-transparent bg-emerald-600 text-white hover:bg-emerald-700',
  danger: 'border-transparent bg-red-600 text-white hover:bg-red-700',
  ghost: 'border-transparent bg-transparent text-gray-600 hover:bg-gray-100 hover:text-gray-900',
  link: 'border-transparent bg-transparent text-brand-primary-ink hover:text-brand-primary-ink-hover',
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'gap-1.5 rounded-lg px-3 py-1.5 text-xs',
  md: 'gap-2 rounded-lg px-4 py-2 text-sm',
  lg: 'gap-2 rounded-lg px-5 py-2.5 text-sm',
  icon: 'h-9 w-9 rounded-lg p-0',
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
