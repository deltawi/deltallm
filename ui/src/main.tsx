import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App'
import { uiMount } from './lib/uiMount'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename={uiMount().mount_path || "/"}>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
