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
' '  the developer test for this bug is: python -m pytest pandas/tests/dtypes/cast/test_downcast.py pandas/tests/frame/methods/test_combine.py pandas/tests/series/indexing/test_setitem.py -k '"'"'out_of_integer_range or within_integer_range or do_round_out_of_range or combine_object_result or out_of_int64_range or within_int64_range'"'"' -v' >&2
    exit 64 ;;
  *)
    # Anything that is not one of the verbs above is run as an ordinary command.
    # Without this, an ENTRYPOINT swallows `docker run <image> sleep infinity` —
    # which is how a harness keeps a container alive — so the container exited
    # immediately and every later `docker exec` failed. A benchmark image should
    # still behave like an image.
    exec "$@" ;;
esac
