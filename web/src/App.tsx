import { Navigate, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Layout from "./components/Layout";
import KBs from "./pages/KBs";
import Docs from "./pages/Docs";
import Chat from "./pages/Chat";
import ACL from "./pages/ACL";
import Ops from "./pages/Ops";
import Monitor from "./pages/Monitor";
import Models from "./pages/Models";
import Files from "./pages/Files";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/kbs" replace />} />
        <Route path="kbs" element={<KBs />} />
        <Route path="kbs/:kbId/docs" element={<Docs />} />
        <Route path="files" element={<Files />} />
        <Route path="chat" element={<Chat />} />
        <Route path="acl" element={<ACL />} />
        <Route path="models" element={<Models />} />
        <Route path="ops" element={<Ops />} />
        <Route path="monitor" element={<Monitor />} />
      </Route>
    </Routes>
  );
}
