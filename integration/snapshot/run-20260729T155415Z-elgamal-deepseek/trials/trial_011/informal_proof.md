  byequiv => //.
    proc.
    seq 5 5 : (={q1,q2,RO_track.bad_grp, RO_track.mp}).
    auto.
    seq 1 1 : (={glob Adv, q1,q2,RO_track.bad_grp, RO_track.mp}).
    call(_ : ={RO_track.bad_grp,RO_track.mp}).
    proc.
    if; progress.
    sp; if; progress.
    auto.
    if; progress.
    auto.
    auto.
    seq 4 3 : (={glob Adv, q1, q2, c, RO_track.bad_grp, RO_track.mp, choice}).
    wp.
    rnd(fun x => t{1} +^ x).
    auto; progress.
    by rewrite -textA textR textC textI.
    apply dtext_uni => //.
    rewrite dtext_fu.
    rewrite dtext_fu.
    by rewrite -textA textR textC textI.
    call(_ : ={RO_track.bad_grp, RO_track.mp}).
    proc.
    if; progress.
    sp; if; progress.
    auto.
    if; progress.
    auto.
    auto.
