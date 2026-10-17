import Phone from "./phone/Phone.jsx";
import Dashboard from "./dashboard/Dashboard.jsx";

/** Tiny path router: /phone and /dashboard (anything else shows links). */
export default function App() {
  const path = window.location.pathname.replace(/\/+$/, "");
  if (path === "/phone") return <Phone />;
  if (path === "/dashboard") return <Dashboard />;
  return (
    <main className="home">
      <h1>Sightline</h1>
      <p><a href="/phone">Phone view</a></p>
      <p><a href="/dashboard">Judge dashboard</a></p>
    </main>
  );
}
