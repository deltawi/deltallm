type UserOption = {
  account_id: string;
  email?: string | null;
  organization_role?: string | null;
  team_role?: string | null;
  already_member?: boolean;
};

interface UserSearchSelectProps {
  search: string;
  onSearchChange: (value: string) => void;
  options: UserOption[];
  loading?: boolean;
  selectedAccountId?: string;
  onSelect: (option: UserOption) => void;
  searchPlaceholder?: string;
  helperText?: string;
  emptyText?: string;
}

export default function UserSearchSelect({
  search,
  onSearchChange,
  options,
  loading = false,
  selectedAccountId,
  onSelect,
  searchPlaceholder = 'Search users...',
  helperText,
  emptyText = 'No users found.',
}: UserSearchSelectProps) {
  return (
    <div className="space-y-2">
      <input
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder={searchPlaceholder}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      {helperText && <p className="text-xs text-gray-500">{helperText}</p>}
      <div className="border border-gray-200 rounded-lg max-h-56 overflow-y-auto bg-white">
        {loading ? (
          <div className="px-3 py-2 text-sm text-gray-500">Searching...</div>
        ) : options.length === 0 ? (
          <div className="px-3 py-2 text-sm text-gray-500">{emptyText}</div>
        ) : (
          options.map((option) => {
            const isSelected = selectedAccountId === option.account_id;
            return (
              <button
                key={option.account_id}
                type="button"
                onClick={() => onSelect(option)}
                className={`w-full text-left px-3 py-2 border-b border-gray-100 last:border-b-0 hover:bg-blue-50 transition-colors ${
                  isSelected ? 'bg-blue-50' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-gray-900">{option.email || option.account_id}</span>
                  {option.already_member && (
                    <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">Already in team</span>
                  )}
                </div>
                <div className="text-xs text-gray-500 mt-0.5">{option.account_id}</div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
