import type { IFilterItem } from '../model/types.d'

type FlatItem = Omit<IFilterItem, 'items'> & { items?: FlatItem[] }

// The browse API returns filter items as a flat list where `level` gives the depth
// and children follow their parent. Nest them so every item has its children in `items`.
export function convertFlatToTree(items: FlatItem[] = []): IFilterItem[] {
  const roots: IFilterItem[] = []
  const stack: IFilterItem[] = []

  items.forEach((flatItem) => {
    const item: IFilterItem = {
      ...flatItem,
      items: flatItem.items?.length ? convertFlatToTree(flatItem.items) : [],
    }
    const level = Number(item.level) || 0

    while (stack.length && (Number(stack[stack.length - 1].level) || 0) >= level) stack.pop()

    if (stack.length) stack[stack.length - 1].items.push(item)
    else roots.push(item)

    stack.push(item)
  })

  return roots
}
