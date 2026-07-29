export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const c = {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  }
  switch (name) {
    case 'chat': return (<svg {...c}><path d="M21 11.5a8.38 8.38 0 0 1-8.5 8.5 9 9 0 0 1-4-1L3 20l1.5-4.5a8.38 8.38 0 0 1-1-4A8.5 8.5 0 1 1 21 11.5z" /></svg>)
    case 'calendar': return (<svg {...c}><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M3 9h18M8 2v4M16 2v4" /></svg>)
    case 'note': return (<svg {...c}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h6" /></svg>)
    case 'memory': return (<svg {...c}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>)
    case 'skill': return (<svg {...c}><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></svg>)
    case 'knowledge': return (<svg {...c}><path d="M4 4h11a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3z" /><path d="M4 4v13" /><path d="M8 8h6M8 12h6" /></svg>)
    case 'goal': return (<svg {...c}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.6" fill="currentColor" /></svg>)
    default: return null
  }
}
