export type Platform = 'telegram' | 'whatsapp' | 'facebook'
export type Permission = 'unknown' | 'allowed' | 'forbidden'
export type Category = {
  id: number; name: string; color: string; emoji: string; keywords: string[]; excluded_keywords: string[];
  platforms: Platform[]; search_enabled: boolean; frequency_hours: number;
  last_searched_at: string | null; created_at: string
}
export type Link = {
  id: number; category_id: number | null; platform: Platform; url: string; name: string;
  image_url: string | null; members: number | null; description: string; source: string;
  status: string; permission: Permission; favorite: boolean; archived: boolean; notes: string;
  last_checked_at: string | null; last_posted_at: string | null; created_at: string
}
export type Caption = { id: number; category_id: number | null; name: string; text: string; offer_url: string; created_at: string }
export type Stats = {
  links: number; favorites: number; allowed: number; posted: number; leads: number; sales: number;
  by_platform: Record<string,number>; by_status: Record<string,number>;
  categories: { id:number; name:string; emoji:string; total:number }[];
  recent_jobs: {id:number; status:string; kind:string; added:number}[]
}
export type Post = {id:number; link_id:number; name:string; platform:Platform; posted_at:string; leads:number; sales:number; notes:string}
export type JobProgress = {status:string; processed:number; total:number; added:number; errors:number; message:string; level:string}
