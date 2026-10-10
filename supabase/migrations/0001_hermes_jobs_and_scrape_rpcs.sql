-- The scraper service's job-control RPCs (service-role only).
create or replace function public.rpc_claim_scrape_job()
returns jsonb language plpgsql security definer set search_path = public
as $$
declare j record;
begin
  select id, rpu, media into j
  from hermes.jobs
  where state = 'queued'
  order by created_at
  for update skip locked
  limit 1;
  if not found then
    return null;
  end if;
  update hermes.jobs
     set state = 'running', updated_at = now()
   where id = j.id;
  return jsonb_build_object('job_id', j.id, 'rpu', j.rpu, 'media', j.media);
end $$;

create or replace function public.rpc_finish_scrape_job(
  p_id uuid, p_state text, p_error_class text default null,
  p_detail text default null, p_summary jsonb default null)
returns jsonb language plpgsql security definer set search_path = public
as $$
begin
  update hermes.jobs
     set state = p_state, error_class = p_error_class,
         detail = p_detail, summary = p_summary, updated_at = now()
   where id = p_id;
  return jsonb_build_object('ok', true);
end $$;

alter table hermes.jobs
  add column if not exists media text,
  add column if not exists detail text,
  add column if not exists summary jsonb;

revoke execute on function public.rpc_claim_scrape_job() from public, anon, authenticated;
revoke execute on function public.rpc_finish_scrape_job(uuid, text, text, text, jsonb) from public, anon, authenticated;
grant execute on function public.rpc_claim_scrape_job() to service_role;
grant execute on function public.rpc_finish_scrape_job(uuid, text, text, text, jsonb) to service_role;
