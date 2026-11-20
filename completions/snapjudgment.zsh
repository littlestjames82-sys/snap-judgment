#compdef snapjudgment snap-judgment
# Zsh completion for snapjudgment (Snap Judgment).
#
# Install: copy this file as _snapjudgment into a directory on your
# $fpath, e.g. ~/.zsh/completions/_snapjudgment, then run compinit.

_snapjudgment() {
    _arguments -s \
        '--all[treat every image in the folders as a screenshot]' \
        '--json[print the verdict as JSON instead of the report]' \
        '--card=[write a self-contained HTML share card to FILE]:file:_files' \
        '--svg=[write an SVG share card (1200x630) to FILE]:file:_files' \
        '--png=[write a PNG share card (1200x630) to FILE]:file:_files' \
        '--out=[write a Markdown report to FILE]:file:_files' \
        '--html=[write a full HTML report to FILE]:file:_files' \
        '--since=[only count screenshots on or after this date (a bare year works too)]:date:' \
        '--until=[only count screenshots on or before this date (a bare year works too)]:date:' \
        '--year=[only count screenshots from this single year]:year:' \
        '--plea-bargain[also compute the hypothetical verdict if duplicates and old screenshots were deleted]' \
        '--plea-years=[with --plea-bargain, screenshots older than this many years would go]:years:' \
        '--rematch[compare this hearing against the last court record for the same folders]' \
        '--badge=[write a shields-style verdict badge (SVG) to FILE]:file:_files' \
        '--vs=[also judge FOLDER as a co-defendant and compare]:folder:_files -/' \
        '--vs-year=[compare year YYYY against the most recent other year in the same folders]:year:' \
        '--wrapped-kit=[write a Wrapped kit (SVG card + post drafts) for the current year into DIR]:directory:_files -/' \
        '--fail-above=[exit 1 when the verdict ranks above this tier]:tier:("Innocent Bystander" "Petty Offender" "Repeat Offender" "Habitual Hoarder" "Felony-Level Archivist" "Unrepentant Screenshot Felon")' \
        '--judge=[the presiding judge voice]:judge:(strict fair lenient)' \
        '--judge-file=[load a custom judge voice from a JSON pack]:file:_files' \
        '--oneline[print exactly one summary line (ignored when --json is given)]' \
        '--csv=[write per-file records to FILE as CSV]:file:_files' \
        '--history[print the court record (prior hearings) and exit]' \
        '--version[print the version and exit]' \
        '--help[print the help and exit]' \
        '*:folder:_files -/'
}

_snapjudgment "$@"
