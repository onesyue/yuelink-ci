# Real source gate fixture

These two scripts are unmodified copies of `onesyue/yuelink` commit
`a0c86ccb0310738bd7c26a187dc7dc8d305b55d0` (PR #51). They run against temporary Git histories;
no checker output is stubbed. Refresh both from a reviewed source commit when
the companion implementation changes. Set `SOURCE_VERSION_GATE_REPO` to run the
same integration regression against a live source checkout instead.
