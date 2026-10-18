import CodingAgentsAdmin from '../components/coding-agents/CodingAgentsAdmin';
import ConnectCodingAgent from '../components/coding-agents/ConnectCodingAgent';
import { useAuth } from '../lib/auth';

// Key admins set up developers. Other members connect their own agent with a self-service key.
export default function CodingAgents() {
  const { session, authMode } = useAuth();
  const isKeyAdmin = authMode === 'master_key'
    || session?.role === 'platform_admin'
    || Boolean(session?.effective_permissions?.includes('key.update'));
  return isKeyAdmin ? <CodingAgentsAdmin /> : <ConnectCodingAgent />;
}
