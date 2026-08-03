   proc.
   if{1}.
   sp.
   if.
   progress.
   auto.
   progress.
(* print/search removed for agent sandbox: avoids oversized goal dumps *)
   rewrite mem_fdom mem_set //.
   skip; progress.
   by rewrite mem_fdom H.
   if.
   progress.
   auto.
   progress.
   rewrite !mem_fdom mem_set.
   smt().
   auto.
