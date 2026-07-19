import { Route, Routes } from "react-router-dom";
import { Layout } from "./components";
import {
  AdminPage,
  CalendarPage,
  CircuitDetailPage,
  CircuitsPage,
  DriversPage,
  EventPage,
  HomePage,
  LivePage,
  SessionPage,
  StandingsPage,
  TeamsPage,
} from "./pages";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/live" element={<LivePage />} />
        <Route path="/calendar" element={<CalendarPage />} />
        <Route path="/events/:season/:round" element={<EventPage />} />
        <Route path="/standings" element={<StandingsPage />} />
        <Route path="/drivers" element={<DriversPage />} />
        <Route path="/teams" element={<TeamsPage />} />
        <Route path="/circuits" element={<CircuitsPage />} />
        <Route path="/circuits/:slug" element={<CircuitDetailPage />} />
        <Route path="/sessions/:sessionId" element={<SessionPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="*" element={<HomePage />} />
      </Routes>
    </Layout>
  );
}
