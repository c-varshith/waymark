export interface ActivityEvent {
  id?: number | string | null
  event: string
  data: Record<string, unknown>
}
export interface ActivityCell {
  cell_id: string
  locality?: string | null
  street_name?: string | null
}
export interface ActivityEntry {
  id: string
  cell_id: string
  text: string
  at: string
}
export function activityEntry(event: ActivityEvent, cell: ActivityCell | undefined, previousScore?: number): ActivityEntry | null
export function activityAge(value: string, now?: number): string
