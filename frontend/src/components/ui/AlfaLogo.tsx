// Условный знак для прототипа: красный квадрат с буквой «А».
export function AlfaLogo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 36 36" aria-hidden="true" focusable="false">
      <rect width="36" height="36" rx="9" fill="var(--accent)" />
      <path d="M18 7.5 10.5 27h3.6l1.7-4.6h4.4l1.7 4.6h3.6L18 7.5Zm-1.1 11.8L18 16l1.1 3.3h-2.2Z" fill="#fff" />
      <rect x="10.5" y="28.5" width="15" height="2.4" rx="1" fill="#fff" />
    </svg>
  );
}
