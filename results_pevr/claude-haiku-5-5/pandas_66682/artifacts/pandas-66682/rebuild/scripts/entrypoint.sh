#!/usr/bin/env bash
set -uo pipefail
case "${1:-validate}" in
  buggy)                   shift; exec /benchmark/scripts/run_buggy.sh "$@" ;;
  fixed)                   shift; exec /benchmark/scripts/run_fixed.sh "$@" ;;
  validate)                shift; exec /benchmark/scripts/validate.sh "$@" ;;
  evaluate-generated-test) shift; exec /benchmark/scripts/evaluate_generated_test.sh "$@" ;;
  metadata)                exec cat /benchmark/metadata.json ;;
  shell)                   shift; exec bash "$@" ;;
  "")
    echo "usage: docker run <image> [buggy|fixed|validate|evaluate-generated-test|metadata|shell]" >&2
    # The selector is arbitrary shell text. Interpolated into a double-quoted
    # echo, numpy#29372's `python -c "import numpy, sys; …"` closed the string
    # and bash refused the whole file — breaking every entrypoint command for
    # that image, not just the usage message. printf with a single-quoted
    # argument cannot be terminated from inside.
    printf '%s
' '  the developer test for this bug is: python -m pytest pandas/tests/arithmetic/test_period.py -k "test_pi_sub_pi_count_on_nat_sentinel or test_pi_sub_period_count_on_nat_sentinel or test_pi_sub_pi_still_raises_on_overflow or test_pi_sub_pi_nat_still_propagates or test_pi_add_int_still_raises_on_nat_sentinel" -v' >&2
    exit 64 ;;
  *)
    # Anything that is not one of the verbs above is run as an ordinary command.
    # Without this, an ENTRYPOINT swallows `docker run <image> sleep infinity` —
    # which is how a harness keeps a container alive — so the container exited
    # immediately and every later `docker exec` failed. A benchmark image should
    # still behave like an image.
    exec "$@" ;;
esac
