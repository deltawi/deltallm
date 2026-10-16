import type { ReactNode } from 'react';
import SideDrawer from '../SideDrawer';

type TierEditorDrawerProps = {
  title: string;
  description: string;
  saving: boolean;
  onClose: () => void;
  children: ReactNode;
};

export default function TierEditorDrawer(props: TierEditorDrawerProps) {
  return <SideDrawer {...props} />;
}
