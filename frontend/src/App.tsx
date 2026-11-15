import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { Empty, Spinner } from "./ui";
import { Guard, Shell } from "./components/Shell";
import { Login } from "./pages/Login";
import { ForcedPassword, Profile } from "./pages/Profile";
import { Dashboard } from "./pages/Dashboard";
import { Vehicles } from "./pages/Vehicles";
import { VehicleCard } from "./pages/VehicleCard";
import { Requests } from "./pages/Requests";
import { RequestCard } from "./pages/RequestCard";
import { Waybills } from "./pages/Waybills";
import { WaybillCard } from "./pages/WaybillCard";
import { ReadingsReview } from "./pages/ReadingsReview";
import { Employees } from "./pages/Employees";
import { Entities } from "./pages/Entities";
import { Users } from "./pages/Users";
import { Roles } from "./pages/Roles";
import { Audit } from "./pages/Audit";
import { Backups } from "./pages/Backups";
import { SearchX } from "lucide-react";
import { DriverFile, DriverFiles } from "./pages/dossier/DriverFiles";
import { VehicleFile, VehicleFiles } from "./pages/dossier/VehicleFiles";
import { Registry } from "./pages/Registry";
import { SECTIONS } from "./sections";
import { tradeActions } from "./pages/tradeActions";
import { PrintPage } from "./pages/Print";
import { Calendar } from "./pages/Calendar";
import { Reminders } from "./pages/Reminders";
import { Notifications } from "./pages/Notifications";
import { AnnouncementCard, Announcements } from "./pages/Announcements";
import { News, NewsCard } from "./pages/News";
import { Timesheet } from "./pages/Timesheet";
import { TimesheetPrint } from "./pages/TimesheetPrint";
import { JobsBoard, MyJobs } from "./pages/Jobs";
import { DriverEntries, EntriesFeed } from "./pages/Entries";
import { Roster } from "./pages/Roster";
import { DriverHome } from "./pages/DriverHome";
import { OutboxPage } from "./pages/OutboxPage";
import { Purchases } from "./pages/Purchases";
import { Bills } from "./pages/Bills";
import { BillForm } from "./pages/BillForm";
import { BillCard } from "./pages/BillCard";

const VEH = ["vehicles.view", "vehicles.view_own"];
const REQ = ["requests.view", "requests.view_own"];
const WB = ["waybills.view", "waybills.view_own"];

export function App() {
  const { me, loading, canAny } = useAuth();
  const loc = useLocation();
  if (loading) return <Spinner label="Запуск…" />;
  if (!me) return loc.pathname === "/login" ? <Login /> : <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  if (me.must_change_password) return <ForcedPassword />;

  // у водителя (своя техника, но не весь парк) главная — его кабинет; с главной людей без права на неё отправляем в первый доступный раздел
  const isDriver = canAny("vehicles.view_own") && !canAny("vehicles.view");
  const home = isDriver ? <DriverHome /> : canAny("dashboard.view") ? <Dashboard /> : canAny(...REQ) ? <Navigate to="/requests" replace /> : canAny(...VEH) ? <Navigate to="/vehicles" replace /> : <Navigate to="/profile" replace />;

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/print/timesheet" element={<Guard any={["timesheet.view"]}><TimesheetPrint /></Guard>} />
      <Route path="/print/:kind/:id" element={<PrintPage />} />
      <Route element={<Shell />}>
        <Route index element={home} />
        <Route path="vehicles" element={<Guard any={VEH}><Vehicles /></Guard>} />
        <Route path="vehicles/:id" element={<Guard any={VEH}><VehicleCard /></Guard>} />
        <Route path="requests" element={<Guard any={REQ}><Requests /></Guard>} />
        <Route path="requests/:id" element={<Guard any={REQ}><RequestCard /></Guard>} />
        <Route path="waybills" element={<Guard any={WB}><Waybills /></Guard>} />
        <Route path="waybills/:id" element={<Guard any={WB}><WaybillCard /></Guard>} />
        <Route path="readings" element={<Guard any={["readings.review"]}><ReadingsReview /></Guard>} />
        <Route path="jobs" element={<Guard any={["jobs.view"]}><JobsBoard /></Guard>} />
        <Route path="jobs/mine" element={<Guard any={["jobs.view_own"]}><MyJobs /></Guard>} />
        <Route path="entries" element={<Guard any={["trips.view_own", "fuel_fills.view_own"]}><DriverEntries /></Guard>} />
        <Route path="entries/feed" element={<Guard any={["trips.view", "fuel_fills.view"]}><EntriesFeed /></Guard>} />
        <Route path="timesheet" element={<Guard any={["timesheet.view"]}><Timesheet /></Guard>} />
        <Route path="purchases" element={<Guard any={["purchases.view"]}><Purchases /></Guard>} />
        <Route path="bills" element={<Guard any={["bills.view"]}><Bills /></Guard>} />
        <Route path="bills/new" element={<Guard any={["bills.create"]}><BillForm /></Guard>} />
        <Route path="bills/:id" element={<Guard any={["bills.view"]}><BillCard /></Guard>} />
        <Route path="bills/:id/edit" element={<Guard any={["bills.edit"]}><BillForm /></Guard>} />
        <Route path="roster" element={<Guard any={["roster.view"]}><Roster /></Guard>} />
        <Route path="outbox" element={<OutboxPage />} />
        <Route path="calendar" element={<Guard any={["calendar.view"]}><Calendar /></Guard>} />
        <Route path="reminders" element={<Guard any={["reminders.view"]}><Reminders /></Guard>} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="announcements" element={<Guard any={["announcements.view"]}><Announcements /></Guard>} />
        <Route path="announcements/:id" element={<Guard any={["announcements.view"]}><AnnouncementCard /></Guard>} />
        <Route path="news" element={<Guard any={["announcements.read_own"]}><News /></Guard>} />
        <Route path="news/:id" element={<Guard any={["announcements.read_own"]}><NewsCard /></Guard>} />
        <Route path="employees" element={<Guard any={["employees.view"]}><Employees /></Guard>} />
        <Route path="dossier/employees" element={<Guard any={["employee_files.view"]}><DriverFiles /></Guard>} />
        <Route path="dossier/employees/:id" element={<Guard any={["employee_files.view"]}><DriverFile /></Guard>} />
        <Route path="dossier/vehicles" element={<Guard any={["vehicle_files.view"]}><VehicleFiles /></Guard>} />
        <Route path="dossier/vehicles/:id" element={<Guard any={["vehicle_files.view"]}><VehicleFile /></Guard>} />
        {SECTIONS.map((x) => (
          <Route key={x.key} path={x.path} element={<Guard any={[`${x.key}.view`]}><Registry resource={x.key} sub={x.sub} icon={x.icon} emptyHint={x.empty} actions={tradeActions(x.key)} /></Guard>} />
        ))}
        <Route path="entities" element={<Guard any={["legal_entities.view"]}><Entities /></Guard>} />
        <Route path="users" element={<Guard any={["users.view"]}><Users /></Guard>} />
        <Route path="roles" element={<Guard any={["users.view"]}><Roles /></Guard>} />
        <Route path="audit" element={<Guard any={["audit.view"]}><Audit /></Guard>} />
        <Route path="backups" element={<Guard any={["backups.manage"]}><Backups /></Guard>} />
        <Route path="profile" element={<Profile />} />
        <Route path="*" element={<Empty icon={SearchX} title="Страница не найдена">Проверьте адрес или выберите раздел в меню слева.</Empty>} />
      </Route>
    </Routes>
  );
}
