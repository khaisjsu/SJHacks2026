import {
  Shield,
  Upload,
  FolderLock,
  Settings,
  ScanSearch,
} from 'lucide-react';

interface SidebarProps {
  activeView: string;
  onViewChange: (view: string) => void;
  photoCount: number;
}

export function Sidebar({
  activeView,
  onViewChange,
  photoCount,
}: SidebarProps) {
  const menuItems = [
    { id: 'upload', label: 'Upload / Protect', icon: Upload },
    { id: 'vault', label: 'My Files / Vault', icon: FolderLock, count: photoCount },
    { id: 'verify', label: 'Verify Ownership', icon: ScanSearch },
    { id: 'settings', label: 'Settings', icon: Settings },
  ];

  return (
    <div className="w-72 bg-card border-r border-border h-full flex flex-col">
      <div className="p-6 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-primary flex items-center justify-center">
            <Shield className="w-6 h-6 text-primary-foreground" />
          </div>
          <div>
            <h1 className="text-foreground">PhotoGuard</h1>
            <p className="text-xs text-muted-foreground">Protection workspace</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 overflow-auto p-4">
        <div className="space-y-2">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeView === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onViewChange(item.id)}
                className={`w-full flex items-center justify-between px-4 py-3 rounded-lg transition-colors text-left ${
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                }`}
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Icon className="w-5 h-5 shrink-0" />
                  <span className="truncate">{item.label}</span>
                </div>
                {item.count !== undefined && item.count > 0 && (
                  <span className={`text-xs px-2 py-0.5 rounded-full ${
                    isActive ? 'bg-primary-foreground/20' : 'bg-muted'
                  }`}>
                    {item.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </nav>

      <div className="p-4 border-t border-border">
        <div className="bg-accent rounded-lg p-4">
          <p className="text-sm text-accent-foreground mb-1">Core workspace</p>
          <p className="text-xs text-muted-foreground">
            Upload files, protect them, and manage your protected library in one place.
          </p>
        </div>
      </div>
    </div>
  );
}
