  byphoare => //.
    proc.
    swap 7 3.
    rnd (pred1 guess).
    call(_: true).
    apply Adv_guess_ll.
    apply RO_track_f_ll.
    auto.
    call(_: true).
    apply Adv_choose_ll.
    apply RO_track_f_ll.
    auto; progress.
    by rewrite dexp_ll.
    by rewrite dtext_ll.
    smt. (* prove that {0,1}'s result is 1/2*)
    trivial.
