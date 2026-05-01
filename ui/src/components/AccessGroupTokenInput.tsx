import {
  forwardRef,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from 'react';
import { X } from 'lucide-react';
import { callableTargets, type CallableTargetAccessGroupListItem, type Paginated } from '../lib/api';
import { useApi } from '../lib/hooks';

const ACCESS_GROUP_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const MAX_ACCESS_GROUPS = 100;
const SUGGESTION_LIMIT = 20;
const EMPTY_ACCESS_GROUP_PAGE: Paginated<CallableTargetAccessGroupListItem> = {
  data: [],
  pagination: { total: 0, limit: SUGGESTION_LIMIT, offset: 0, has_more: false },
};

interface AccessGroupTokenInputProps {
  value: string;
  onChange: (value: string) => void;
  ariaLabel?: string;
  placeholder?: string;
}

export interface AccessGroupTokenInputHandle {
  validateAndCommit: () => { valid: boolean; value: string; message?: string };
}

function normalizeAccessGroupKey(value: string): string {
  return value.trim().toLowerCase();
}

function parseAccessGroupValue(value: string): string[] {
  const groups: string[] = [];
  const seen = new Set<string>();

  for (const item of value.split(',')) {
    const groupKey = normalizeAccessGroupKey(item);
    if (!groupKey || seen.has(groupKey)) continue;
    seen.add(groupKey);
    groups.push(groupKey);
  }

  return groups.slice(0, MAX_ACCESS_GROUPS);
}

function accessGroupValue(groups: string[]): string {
  return groups.join(', ');
}

function validationMessageFor(groupKey: string): string | null {
  if (!groupKey) return null;
  if (!ACCESS_GROUP_PATTERN.test(groupKey)) {
    return "Use lowercase letters, numbers, '.', '_' or '-', starting with a letter or number.";
  }
  return null;
}

function suggestionLabel(item: CallableTargetAccessGroupListItem): string {
  const details = [
    item.member_count === 1 ? '1 member' : `${item.member_count} members`,
    item.binding_count === 1 ? '1 grant' : `${item.binding_count} grants`,
  ];
  return details.join(' · ');
}

const AccessGroupTokenInput = forwardRef<AccessGroupTokenInputHandle, AccessGroupTokenInputProps>(function AccessGroupTokenInput({
  value,
  onChange,
  ariaLabel = 'Access Groups',
  placeholder = 'premium, beta, internal',
}, ref) {
  const [draft, setDraft] = useState('');
  const [search, setSearch] = useState('');
  const [focused, setFocused] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [message, setMessage] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const inputId = useId();
  const listboxId = useId();
  const messageId = useId();

  const selectedGroups = useMemo(() => parseAccessGroupValue(value), [value]);
  const selectedSet = useMemo(() => new Set(selectedGroups), [selectedGroups]);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(normalizeAccessGroupKey(draft)), 250);
    return () => window.clearTimeout(timer);
  }, [draft]);

  const { data: accessGroupPage, error: accessGroupError, loading: accessGroupLoading } = useApi(
    () => focused
      ? callableTargets.listAccessGroups({
          search: search || undefined,
          include_members: false,
          limit: SUGGESTION_LIMIT,
          offset: 0,
        })
      : Promise.resolve(EMPTY_ACCESS_GROUP_PAGE),
    [focused, search],
  );

  const suggestions = useMemo(
    () => (accessGroupPage?.data || []).filter((item) => !selectedSet.has(item.group_key)),
    [accessGroupPage?.data, selectedSet],
  );
  const hasSuggestionError = Boolean(accessGroupError);
  const showDropdown = focused && listOpen && (accessGroupLoading || suggestions.length > 0 || hasSuggestionError);
  const activeSuggestionIndex = listOpen && activeIndex >= 0 && activeIndex < suggestions.length
    ? activeIndex
    : -1;
  const activeOptionId = activeSuggestionIndex >= 0
    ? `${listboxId}-option-${activeSuggestionIndex}`
    : undefined;

  const updateGroups = (groups: string[]) => {
    onChange(accessGroupValue(groups));
  };

  const addGroups = (rawItems: string[]): { valid: boolean; value: string; changed: boolean; message?: string } => {
    const next = [...selectedGroups];
    const seen = new Set(next);
    let rejectedMessage: string | null = null;
    let validInputHandled = false;

    for (const rawItem of rawItems) {
      const groupKey = normalizeAccessGroupKey(rawItem);
      if (!groupKey) continue;

      const invalidMessage = validationMessageFor(groupKey);
      if (invalidMessage) {
        rejectedMessage = invalidMessage;
        continue;
      }

      validInputHandled = true;
      if (seen.has(groupKey)) continue;
      if (next.length >= MAX_ACCESS_GROUPS) {
        rejectedMessage = `Access groups are limited to ${MAX_ACCESS_GROUPS} values.`;
        break;
      }

      seen.add(groupKey);
      next.push(groupKey);
    }

    const changed = next.length !== selectedGroups.length;
    const nextValue = accessGroupValue(next);
    if (changed) {
      onChange(nextValue);
    }
    if (changed || (validInputHandled && !rejectedMessage)) {
      setDraft('');
      setSearch('');
    }
    setMessage(rejectedMessage);
    return {
      valid: !rejectedMessage,
      value: nextValue,
      changed,
      message: rejectedMessage || undefined,
    };
  };

  const commitDraft = (): boolean => {
    const result = addGroups([draft]);
    if (result.changed) {
      setListOpen(false);
    }
    return result.changed;
  };

  useImperativeHandle(ref, () => ({
    validateAndCommit: () => {
      const invalidSelectedGroup = selectedGroups.find((groupKey) => validationMessageFor(groupKey));
      if (invalidSelectedGroup) {
        const invalidMessage = validationMessageFor(invalidSelectedGroup) || 'Fix the invalid access group before saving.';
        setMessage(invalidMessage);
        return { valid: false, value: accessGroupValue(selectedGroups), message: invalidMessage };
      }

      if (!draft.trim()) {
        const currentValue = accessGroupValue(selectedGroups);
        setMessage(null);
        return { valid: true, value: currentValue };
      }

      const result = addGroups([draft]);
      if (result.valid) {
        setListOpen(false);
      }
      return {
        valid: result.valid,
        value: result.value,
        message: result.message,
      };
    },
  }));

  const removeGroup = (groupKey: string) => {
    updateGroups(selectedGroups.filter((item) => item !== groupKey));
    setMessage(null);
    inputRef.current?.focus();
  };

  const selectSuggestion = (groupKey: string) => {
    addGroups([groupKey]);
    setListOpen(false);
    inputRef.current?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === ',') {
      event.preventDefault();
      commitDraft();
      return;
    }

    if (event.key === 'Enter') {
      if (showDropdown && activeSuggestionIndex >= 0) {
        event.preventDefault();
        selectSuggestion(suggestions[activeSuggestionIndex].group_key);
        return;
      }
      if (draft.trim()) {
        event.preventDefault();
        commitDraft();
      }
      return;
    }

    if (event.key === 'Backspace' && !draft && selectedGroups.length > 0) {
      event.preventDefault();
      removeGroup(selectedGroups[selectedGroups.length - 1]);
      return;
    }

    if (event.key === 'ArrowDown' && suggestions.length > 0) {
      event.preventDefault();
      setListOpen(true);
      setActiveIndex(activeSuggestionIndex < 0 ? 0 : (activeSuggestionIndex + 1) % suggestions.length);
      return;
    }

    if (event.key === 'ArrowUp' && suggestions.length > 0) {
      event.preventDefault();
      setListOpen(true);
      setActiveIndex(activeSuggestionIndex < 0 ? suggestions.length - 1 : activeSuggestionIndex - 1);
      return;
    }

    if (event.key === 'Escape') {
      setListOpen(false);
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData.getData('text');
    const start = event.currentTarget.selectionStart ?? draft.length;
    const end = event.currentTarget.selectionEnd ?? start;
    const nextDraft = `${draft.slice(0, start)}${pasted}${draft.slice(end)}`;
    if (!nextDraft.includes(',')) return;

    event.preventDefault();
    addGroups(nextDraft.split(','));
  };

  const handleBlur = () => {
    setFocused(false);
    if (draft.trim()) {
      commitDraft();
    }
  };

  return (
    <div className="relative">
      <div
        className={`flex min-h-[42px] w-full flex-wrap items-center gap-1.5 rounded-lg border bg-white px-2 py-1.5 text-sm focus-within:outline-none focus-within:ring-2 ${
          message
            ? 'border-red-300 focus-within:ring-red-500'
            : 'border-gray-300 focus-within:ring-blue-500'
        }`}
        onClick={() => inputRef.current?.focus()}
      >
        {selectedGroups.map((groupKey) => {
          const invalid = Boolean(validationMessageFor(groupKey));
          return (
            <span
              key={groupKey}
              className={`inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${
                invalid
                  ? 'border-red-200 bg-red-50 text-red-700'
                  : 'border-blue-100 bg-blue-50 text-blue-700'
              }`}
            >
              <span className="truncate">{groupKey}</span>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  removeGroup(groupKey);
                }}
                className={`rounded-full p-0.5 focus:outline-none focus:ring-2 ${
                  invalid
                    ? 'text-red-500 hover:bg-red-100 focus:ring-red-500'
                    : 'text-blue-500 hover:bg-blue-100 focus:ring-blue-500'
                }`}
                aria-label={`Remove access group ${groupKey}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          );
        })}
        <input
          ref={inputRef}
          id={inputId}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setMessage(null);
            setListOpen(true);
            setActiveIndex(-1);
          }}
          onFocus={() => {
            setFocused(true);
            setListOpen(true);
            setActiveIndex(-1);
          }}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          role="combobox"
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-expanded={showDropdown}
          aria-controls={listboxId}
          aria-activedescendant={activeOptionId}
          aria-invalid={Boolean(message)}
          aria-describedby={message ? messageId : undefined}
          placeholder={selectedGroups.length > 0 ? 'Add group' : placeholder}
          className="min-w-[10rem] flex-1 border-0 bg-transparent px-1 py-1 text-sm outline-none placeholder:text-gray-400"
        />
      </div>

      {message && (
        <p id={messageId} className="mt-1 text-xs text-red-600">
          {message}
        </p>
      )}

      {showDropdown && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
        >
          {accessGroupLoading && (
            <div className="px-3 py-2 text-xs text-gray-500">Loading groups...</div>
          )}
          {!accessGroupLoading && hasSuggestionError && (
            <div className="px-3 py-2 text-xs text-gray-500">Suggestions unavailable. Manual entry still works.</div>
          )}
          {!accessGroupLoading && !hasSuggestionError && suggestions.map((item, index) => (
            <button
              key={item.group_key}
              id={`${listboxId}-option-${index}`}
              type="button"
              role="option"
              aria-selected={index === activeSuggestionIndex}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => selectSuggestion(item.group_key)}
              className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm ${
                index === activeSuggestionIndex ? 'bg-blue-50 text-blue-800' : 'text-gray-700 hover:bg-gray-50'
              }`}
            >
              <span className="min-w-0 truncate font-medium">{item.group_key}</span>
              <span className="shrink-0 text-xs text-gray-400">{suggestionLabel(item)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

export default AccessGroupTokenInput;
