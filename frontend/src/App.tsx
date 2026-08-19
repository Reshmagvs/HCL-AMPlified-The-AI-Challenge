import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import ChatPanel from './components/ChatPanel'
import Intake from './pages/Intake'
import Diagnostic from './pages/Diagnostic'
import Path from './pages/Path'
import Dashboard from './pages/Dashboard'
import { useSession } from './lib/store'

/**
 * Routes and the one guard that matters:
 *
 * The `future` flags opt in to React Router v7 behaviour now, which keeps the
 * console clean — a demo that logs warnings reads as unfinished.
 * the diagnostic, path and dashboard
 * screens all assume a committed learner, so an unresolved session is sent back
 * to intake rather than allowed to render against a null id.
 */
function RequireLearner({ children }: { children: React.ReactNode }) {
  const learnerId = useSession((s) => s.learnerId)
  return learnerId === null ? <Navigate to="/" replace /> : <>{children}</>
}

export default function App() {
  return (
    <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Layout>
        <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Intake />} />
          <Route
            path="/diagnostic"
            element={
              <RequireLearner>
                <Diagnostic />
              </RequireLearner>
            }
          />
          <Route
            path="/path"
            element={
              <RequireLearner>
                <Path />
              </RequireLearner>
            }
          />
          <Route
            path="/dashboard"
            element={
              <RequireLearner>
                <Dashboard />
              </RequireLearner>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </ErrorBoundary>
      </Layout>
      <ChatPanel />
    </Router>
  )
}
