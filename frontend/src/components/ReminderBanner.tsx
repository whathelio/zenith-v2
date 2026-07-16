interface ReminderBannerProps {
  content: string
}

export default function ReminderBanner({ content }: ReminderBannerProps) {
  if (!content) return null

  return (
    <div className="reminder-banner">
      <div
        style={{ whiteSpace: 'pre-line' }}
        dangerouslySetInnerHTML={{
          __html: content
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
        }}
      />
    </div>
  )
}
