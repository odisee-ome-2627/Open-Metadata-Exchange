import type { IFilterItem } from '../model/types.d'

type SelectableItem = Pick<IFilterItem, 'isSelected'> & { items?: SelectableItem[] }

// Collect every selected item in a (nested) filter tree, parents before children,
// e.g. the selected framework, area and tag of the standards filter.
export function getSelectedItems<T extends SelectableItem>(items: T[] = []): T[] {
  const selected: T[] = []
  items.forEach((item) => {
    if (item.isSelected) selected.push(item)
    selected.push(...getSelectedItems((item.items || []) as T[]))
  })
  return selected
}
