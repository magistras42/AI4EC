      have ->: x = g ^ log x.
      have ->: g ^ log x = gen (log x).
      by rewrite /gen.
      by rewrite log_gen.
     by rewrite !grexpA expA.
