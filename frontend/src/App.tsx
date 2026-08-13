import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { getToken } from "./lib/api";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";

function Private({ children }: { children: ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <Private>
            <Dashboard />
          </Private>
        }
      />
    </Routes>
  );
}
