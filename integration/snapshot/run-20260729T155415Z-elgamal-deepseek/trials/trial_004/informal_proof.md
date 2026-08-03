      rewrite /gen /log /cancel => q.
      have choice_g2q := choicebP ( gen_rel(g ^ q)) e.
      have /choice_g2q @/gen_rel/inj {2}-> //:
      exists(q' : exp), gen_rel (g^q) q'
      by rewrite /gen_rel; by exists q.
