import { useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import BusinessPage from './pages/BusinessPage'
import ManageBookingPage from './pages/ManageBookingPage'
import AdminPage from './pages/AdminPage'
import AdminLayout from './app/admin/layout'
import DashboardPage from './app/admin/page'
import AppointmentsPage from './app/admin/appointments/page'
import ClientsPage from './app/admin/clients/page'
import ServicesPage from './app/admin/services/page'
import StaffPage from './app/admin/staff/page'
import ProfessionalPage from './pages/ProfessionalPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ProtectedRoute from './components/ProtectedRoute'
import { avisarFinDeSesion } from './services/api'

/**
 * Puente entre el cliente HTTP y el router.
 *
 * `api.ts` no importa `react-router`--el cliente HTTP no deberia saber que existe un
 * router-- asi que avisa por suscripcion y el que navega es este componente, que ya
 * esta dentro de `<BrowserRouter>` en `main.tsx`.
 *
 * **Va dentro de `<Routes>` y no fuera** a proposito. El hook `useNavigate` necesita
 * un router encima, y `App` se renderiza dentro de el. Poner la suscripcion en
 * `main.tsx` seria lo mismo con mas saltos.
 *
 * El aviso no lleva la ruta de la que se viene a proposito: `ProtectedRoute` ya la
 * guarda en `state.from`, y mandarla dos veces--una por el aviso del 401 y otra por el
 * redirect--hace que se pierda la que estaba. Ademas, el 401 no sabe si venia de una
 * ruta protegida--cualquier peticion con token lo puede recibir-- asi que no puede
 * inventar un destino.
 */
function AvisoDeFinDeSesion() {
  const navigate = useNavigate()

  useEffect(() => {
    return avisarFinDeSesion(() => navigate('/login', { replace: true }))
  }, [navigate])

  return null
}

function App() {
  return (
    <>
      <AvisoDeFinDeSesion />
      <Routes>
        {/* Redirect raiz -> login */}
        <Route path="/" element={<Navigate to="/login" replace />} />

        {/* Register */}
        <Route path="/register" element={<RegisterPage />} />

        {/* Login */}
        <Route path="/login" element={<LoginPage />} />

        {/* Gestion de reserva por token */}
        <Route path="/r/:token" element={<ManageBookingPage />} />

        {/* Admin Dashboard (nuevo layout) */}
        <Route
          path="/admin/dashboard"
          element={
            <ProtectedRoute>
              <AdminLayout>
                <DashboardPage />
              </AdminLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/appointments"
          element={
            <ProtectedRoute>
              <AdminLayout>
                <AppointmentsPage />
              </AdminLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/clients"
          element={
            <ProtectedRoute>
              <AdminLayout>
                <ClientsPage />
              </AdminLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/services"
          element={
            <ProtectedRoute>
              <AdminLayout>
                <ServicesPage />
              </AdminLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/staff"
          element={
            <ProtectedRoute>
              <AdminLayout>
                <StaffPage />
              </AdminLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin"
          element={<Navigate to="/admin/dashboard" replace />}
        />

        {/*
          Las rutas del panel pasan por `ProtectedRoute`.
        */}
        <Route
          path="/admin/*"
          element={
            <ProtectedRoute>
              <AdminPage />
            </ProtectedRoute>
          }
        />

        <Route
          path="/panel/*"
          element={
            <ProtectedRoute roles={['professional', 'staff']}>
              <ProfessionalPage />
            </ProtectedRoute>
          }
        />

        {/* Pagina publica de reserva - VA ULTIMA */}
        <Route path="/:slug" element={<BusinessPage />} />
      </Routes>
    </>
  )
}

export default App