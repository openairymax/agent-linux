#!/usr/bin/perl
# ===========================================================================
# maintainers_to_codeowners.pl
# Convert Linux MAINTAINERS format to GitHub CODEOWNERS format
# ===========================================================================
# Authority: docs/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md §2.4.4
# ===========================================================================
# Usage: maintainers_to_codeowners.pl MAINTAINERS > CODEOWNERS
# ===========================================================================

use strict;
use warnings;

my $file = $ARGV[0] or die "Usage: $0 <MAINTAINERS_FILE>\n";
open(my $fh, '<', $file) or die "Cannot open $file: $!\n";

my $in_section = 0;
my $current_path = '';
my @current_maintainers = ();

print "# This file is auto-generated from MAINTAINERS by maintainers_to_codeowners.pl\n";
print "# Do not edit manually. Run 'make codeowners-sync' to regenerate.\n";
print "# Authority: docs/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md\n\n";

while (my $line = <$fh>) {
    chomp $line;

    # Section header (all caps, ends with SUBSYSTEM or LAYER)
    if ($line =~ /^([A-Z][A-Z\s-]+(?:SUBSYSTEM|LAYER))\s*$/) {
        # Flush previous section
        if ($in_section && $current_path && @current_maintainers) {
            print "$current_path " . join(' ', @current_maintainers) . "\n";
        }
        $in_section = 1;
        $current_path = '';
        @current_maintainers = ();
        next;
    }

    next unless $in_section;

    # F: field - files/directories
    if ($line =~ /^F:\s*(.+)$/) {
        my $path = $1;
        $path =~ s/\s+$//;
        $current_path = $path;
    }
    # M: field - maintainer (extract email)
    elsif ($line =~ /^M:\s*.*<(.+@.+)>/) {
        push @current_maintainers, "\@$1";
    }
    # R: field - reviewer (extract email)
    elsif ($line =~ /^R:\s*.*<(.+@.+)>/) {
        push @current_maintainers, "\@$1";
    }
    # O: field - CODEOWNERS projection (already in @format)
    elsif ($line =~ /^O:\s*(.+)/) {
        my $owner = $1;
        $owner =~ s/\s+$//;
        push @current_maintainers, $owner unless grep { $_ eq $owner } @current_maintainers;
    }
}

# Flush last section
if ($in_section && $current_path && @current_maintainers) {
    print "$current_path " . join(' ', @current_maintainers) . "\n";
}

close($fh);
