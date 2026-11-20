# Bash completion for snapjudgment (Snap Judgment).
#
# Install: copy or symlink this file into a bash-completion directory,
# e.g. ~/.local/share/bash-completion/completions/snapjudgment for a
# user install or /etc/bash_completion.d/ system-wide, or simply
# source it from your ~/.bashrc.

_snapjudgment() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    local opts="--court --all --grand-jury --recursive --json --card --svg --png --out --html --since --until --year --plea-bargain --plea-years --cleanup-list --rematch --trend --badge --vs --vs-year --wrapped-kit --fail-above --judge --judge-file --intensity --oneline --csv --history --version --help"

    case "$prev" in
        --court)
            COMPREPLY=( $(compgen -W "screenshots downloads" -- "$cur") )
            return 0
            ;;
        --judge)
            COMPREPLY=( $(compgen -W "strict fair lenient" -- "$cur") )
            return 0
            ;;
        --intensity)
            COMPREPLY=( $(compgen -W "gentle kind honest spicy roast" -- "$cur") )
            return 0
            ;;
        --fail-above)
            COMPREPLY=( $(compgen -W "'Innocent Bystander' 'Petty Offender' 'Repeat Offender' 'Habitual Hoarder' 'Felony-Level Archivist' 'Unrepentant Screenshot Felon'" -- "$cur") )
            return 0
            ;;
        --card|--svg|--png|--out|--html|--badge|--csv|--judge-file|--wrapped-kit|--cleanup-list)
            # options that name a file to write (or, for --judge-file, read)
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
        --vs)
            COMPREPLY=( $(compgen -d -- "$cur") )
            return 0
            ;;
    esac

    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
    else
        # positional arguments are folders
        COMPREPLY=( $(compgen -d -- "$cur") )
    fi
    return 0
}

complete -F _snapjudgment snapjudgment snap-judgment
