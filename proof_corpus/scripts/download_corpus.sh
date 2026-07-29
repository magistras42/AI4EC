#!/bin/bash

# Check if a CSV file was provided as an argument
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <path_to_csv_file.csv>"
    exit 1
fi

INPUT_FILE="$1"

# Check if the provided file actually exists
if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: File '$INPUT_FILE' not found."
    exit 1
fi

# Ensure the eval directory exists
mkdir -p eval

# Process the CSV file line by line
# IFS=',' tells the read command to split by comma
while IFS=',' read -r name url; do
    # Remove any potential carriage returns (useful if file has Windows line endings)
    name=$(echo "$name" | tr -d '\r' | xargs)
    url=$(echo "$url" | tr -d '\r' | xargs)

    # Skip the header row, empty lines, and repositories marked as "Private"
    if [[ -z "$name" || "$name" == "Name" || "$url" == "Private" ]]; then
        continue
    fi

    # Replace spaces with underscores for safe file naming
    safe_name="${name// /_}"
    out_file="../zip/${safe_name}.zip"
    
    # Strip any trailing slashes from the URL
    url="${url%/}"

    echo "Fetching $name..."

    if [[ "$url" == *github.com* ]]; then
        # -f fails silently on server errors (404), allowing the fallback to trigger
        # -s hides the progress bar so our console output stays clean
        if curl -L -f -s -o "$out_file" "${url}/archive/refs/heads/main.zip"; then
            echo "  -> Downloaded main branch"
        elif curl -L -f -s -o "$out_file" "${url}/archive/refs/heads/master.zip"; then
            echo "  -> Downloaded master branch"
        else
            echo "  -> Failed (neither main nor master branch found)"
        fi
    elif [[ "$url" == *gitlab.com* ]]; then
        repo_name=$(basename "$url")
        if curl -L -f -s -o "$out_file" "${url}/-/archive/main/${repo_name}-main.zip"; then
            echo "  -> Downloaded main branch"
        elif curl -L -f -s -o "$out_file" "${url}/-/archive/master/${repo_name}-master.zip"; then
            echo "  -> Downloaded master branch"
        else
            echo "  -> Failed (neither main nor master branch found)"
        fi
    else
        echo "  -> Skipping unrecognized URL format"
    fi

done < "$INPUT_FILE"

echo "Done!"