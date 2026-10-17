import { useState } from 'react';
import Button from '../Button';
import SideDrawer from '../SideDrawer';
import AgentSetupPanel from './AgentSetupPanel';
import { useKeyFirstUse } from './useKeyFirstUse';
import type { ApiKey } from '../../lib/api';
import { codingAgentInfo, type CodingAgentId } from '../../lib/codingAgentSetup';

/** The setup text of an existing key. The key itself is not shown again. */
export default function KeySetupDrawer({ apiKey, onClose }: { apiKey: ApiKey; onClose: () => void }) {
  const [agent, setAgent] = useState<CodingAgentId>(codingAgentInfo(apiKey.coding_agent?.agent).id);
  const lastUsedAt = useKeyFirstUse(apiKey.token, apiKey.last_used_at ?? null);
  return (
    <SideDrawer
      title="Agent setup"
      description={apiKey.key_name || 'Coding agent key'}
      saving={false}
      onClose={onClose}
      size="md"
      overlayLabel="Close setup"
      footer={<Button onClick={onClose}>Done</Button>}
    >
      <AgentSetupPanel
        agent={agent}
        onAgentChange={setAgent}
        rawKey={null}
        mainModel={apiKey.coding_agent?.main_model || ''}
        smallModel={apiKey.coding_agent?.small_model || ''}
        lastUsedAt={lastUsedAt}
      />
    </SideDrawer>
  );
}
