import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: any) {
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, fontFamily: 'monospace', fontSize: 13 }}>
          <h3 style={{ color: '#e74c3c' }}>⚠️ 页面渲染出错</h3>
          <pre style={{ whiteSpace: 'pre-wrap', background: '#f8f8f8', padding: 16, borderRadius: 8, border: '1px solid #ddd', maxHeight: 400, overflow: 'auto' }}>
            {this.state.error.message}
            {'\n\n'}
            {this.state.error.stack}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}
