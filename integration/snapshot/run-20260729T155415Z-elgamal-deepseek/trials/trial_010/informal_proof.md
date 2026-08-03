byequiv => //.
  proc.
  inline*.
  seq 5 5 : (={q1, q2} /\ RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\
    RO_track.bad_grp{1} = g ^ (q1{2} * q2{2}) /\ grp1{2} = g^q1{2} /\ grp2{2} = g^q2{2} /\
    RO_track.badHappened{1} = (g ^ (q1{2} * q2{2}) \in fdom Adv2LCDHAdv.RO_track.mp{2})).
  auto; progress.
  rewrite mem_fdom mem_empty //.
  wp.
  exlim q1{1} => q1_L.
  exlim q2{1} => q2_L.
  call(_: RO_track.badHappened{1} = (g ^ (q1_L * q2_L) \in fdom Adv2LCDHAdv.RO_track.mp{2}) /\
    RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\ RO_track.bad_grp{1} = g ^ (q1_L * q2_L)).
  apply (RO_LCDHAdv q1_L q2_L).
  auto.
  call(_: RO_track.badHappened{1} = (g ^ (q1_L * q2_L) \in fdom Adv2LCDHAdv.RO_track.mp{2}) /\
    RO_track.mp{1} = Adv2LCDHAdv.RO_track.mp{2} /\ RO_track.bad_grp{1} = g ^ (q1_L * q2_L)).
  apply (RO_LCDHAdv q1_L q2_L).
  auto.
