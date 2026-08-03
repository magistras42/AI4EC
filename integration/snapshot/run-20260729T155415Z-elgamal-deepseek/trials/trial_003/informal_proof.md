      rewrite /gen /log /cancel => x.
      have @/gen_rel <-// := choicebP ( gen_rel x) e.
      rewrite generated.
