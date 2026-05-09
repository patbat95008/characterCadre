import { BrowserRouter, Route, Routes } from 'react-router-dom'

import OllamaStatus from './components/OllamaStatus'
import EditCharacters from './pages/EditCharacters'
import EditScenarios from './pages/EditScenarios'
import Game from './pages/Game'
import MainMenu from './pages/MainMenu'
import NewGame from './pages/NewGame'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <OllamaStatus />
        <Routes>
          <Route path="/" element={<MainMenu />} />
          <Route path="/characters" element={<EditCharacters />} />
          <Route path="/scenarios" element={<EditScenarios />} />
          <Route path="/new-game" element={<NewGame />} />
          <Route path="/game/:saveId" element={<Game />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
