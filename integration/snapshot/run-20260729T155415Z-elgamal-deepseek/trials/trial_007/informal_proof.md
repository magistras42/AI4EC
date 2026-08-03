    rewrite (RealOrder.ler_trans Pr[G2.main() @ &m : RO_track.badHappened]).
    byequiv
    (_ : true ==>
     (={badHappened}(RO_track, RO_track)) /\
      (! RO_track.badHappened{2} => ={res})) :
     (RO_track.badHappened) => //.
    conseq G1_G2_eq => //.
    progress; smt().
    by rewrite RealOrder.lerr_eq (G2_bad_ub &m).
