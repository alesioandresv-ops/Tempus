import { Routes, Route } from 'react-router-dom'
import BusinessPage from './pages/BusinessPage'
import ManageBookingPage from './pages/ManageBookingPage'
import AdminPage from './pages/AdminPage'
import ProfessionalPage from './pages/ProfessionalPage'
import LoginPage from './pages/LoginPage'

function App() {
  return (
    <Routes>
      {/* Página pública de reserva */}
      <Route path="/:slug" element={<BusinessPage />} />
      
      {/* Gestión de reserva por token */}
      <Route path="/r/:token" element={<ManageBookingPage />} />
      
      {/* Login */}
      <Route path="/login" element={<LoginPage />} />
      
      {/* Panel admin */}
      <Route path="/admin/*" element={<AdminPage />} />
      
      {/* Panel profesional */}
      <Route path="/panel/*" element={<ProfessionalPage />} />
    </Routes>
  )
}

export default App
